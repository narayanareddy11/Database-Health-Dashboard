# rds_dashboard/handler.py
import os, re
from datetime import datetime, timedelta, timezone
import boto3
from shared.teams import post_to_teams, simple_card
from shared.collectors import get_acct_title

# ------------- UI helpers -------------
def _cell(text, *, bold=False, color=None, width="auto", wrap=False):
    block = {"type": "TextBlock", "text": str(text), "wrap": bool(wrap),
             "maxLines": 1 if not wrap else 0, "size": "Small", "spacing": "Small"}
    if bold: block["weight"] = "Bolder"
    if color: block["color"] = color
    return {"type": "Column", "width": width, "items": [block]}

def _dot_by(level): return {"OK": "🟢", "WARN": "🟡", "ALERT": "🔴"}.get(level, "")
def _fmt_pct(v):    return "N/A" if v is None else f"{float(v):.0f}%"
def _fmt_ms(v):     return "N/A" if v is None else f"{float(v):.0f} ms"

def _rds_link(region, dbid):
    return f"https://{region}.console.aws.amazon.com/rds/home?region={region}#database:id={dbid}"

# ------------- CloudWatch helpers -------------
def _cw_latest(cw, ns, metric, dims, minutes=15, period=300, stat="Average"):
    try:
        end = datetime.now(timezone.utc); start = end - timedelta(minutes=minutes)
        r = cw.get_metric_statistics(Namespace=ns, MetricName=metric, Dimensions=dims,
                                     StartTime=start, EndTime=end, Period=period, Statistics=[stat])
        dps = r.get("Datapoints", [])
        if not dps: return None
        return max(dps, key=lambda x: x["Timestamp"]).get(stat)
    except Exception:
        return None

def _list_db_instances(rds):
    out = []; p = rds.get_paginator("describe_db_instances")
    for page in p.paginate(): out.extend(page.get("DBInstances", []))
    return out

def _get_name_tag(rds, arn):
    try:
        tags = rds.list_tags_for_resource(ResourceName=arn).get("TagList", [])
        return next((t["Value"] for t in tags if t.get("Key") == "Name"), None)
    except Exception:
        return None

# ------------- Email helpers -------------
def _parse_emails(value: str):
    """
    Split emails by comma/semicolon/space/newline and strip blanks.
    Example: "a@x.com, b@x.com; c@x.com d@x.com"
    """
    parts = re.split(r"[,\s;]+", value or "")
    # de-duplicate while preserving order
    seen = set()
    out = []
    for p in parts:
        e = p.strip()
        if e and e.lower() not in seen:
            seen.add(e.lower())
            out.append(e)
    return out

def _send_email_ses(session, region, subject, html, text, env):
    if env.get("ENABLE_MAIL_REPORT","false").lower() not in ("1","true","t","yes","y"):
        return
    frm = env.get("MAIL_FROM","").strip()
    tos = _parse_emails(env.get("MAIL_TO",""))
    ccs = _parse_emails(env.get("MAIL_CC",""))
    bccs = _parse_emails(env.get("MAIL_BCC",""))

    if not frm or not tos:
        return

    ses = session.client("ses", region_name=region)
    dest = {"ToAddresses": tos}
    if ccs: dest["CcAddresses"] = ccs
    if bccs: dest["BccAddresses"] = bccs

    ses.send_email(
        Source=frm,
        Destination=dest,
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {
                "Html": {"Data": html, "Charset": "UTF-8"},
                "Text": {"Data": text, "Charset": "UTF-8"}
            }
        }
    )

def _build_email_html(title, rows):
    # rows: (db_cell_email, engine, public_cell, enc_cell, write_cell, read_cell, cpu_cell, free_cell, conns_cell, pending_cell, autoscale_cell, db_cell_teams)
    head = """<style>
      table{border-collapse:collapse;width:100%;font:13px Arial}
      th,td{border:1px solid #ddd;padding:6px 8px}
      th{background:#f5f5f5;text-align:left}
      .right{text-align:right}
    </style>"""
    tr=[]
    for r in rows:
        tr.append(
            "<tr>"
            f"<td>{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td><td>{r[3]}</td>"
            f"<td class='right'>{r[4]}</td><td class='right'>{r[5]}</td>"
            f"<td class='right'>{r[6]}</td><td class='right'>{r[7]}</td><td class='right'>{r[8]}</td>"
            f"<td>{r[9]}</td><td>{r[10]}</td>"
            "</tr>"
        )
    html = (
        f"<html><head>{head}</head><body>"
        f"<h3>{title}</h3>"
        "<table>"
        "<tr><th>DB</th><th>Engine</th><th>Public</th><th>Encryption</th>"
        "<th>Write latency</th><th>Read latency</th><th>CPU</th><th>Free space</th>"
        "<th>Connections</th><th>Pending Maint</th><th>Autoscaling</th></tr>"
        + "\n".join(tr) + "</table></body></html>"
    )
    txt = title + "\n" + "\n".join([", ".join(map(str, r[:11])) for r in rows])
    return html, txt

# ------------- main entry -------------
def run(session, webhook, region, env):
    acct = get_acct_title(session)
    rds = session.client("rds", region_name=region)
    cw  = session.client("cloudwatch", region_name=region)

    lookback_min = int(env.get("METRIC_LOOKBACK_MIN","15"))
    period_sec   = int(env.get("METRIC_PERIOD_SEC","300"))
    WRITE_WARN=float(env.get("WRITE_LAT_WARN","200"))
    WRITE_ALERT=float(env.get("WRITE_LAT_ALERT","300"))
    READ_WARN=float(env.get("READ_LAT_WARN","200"))
    READ_ALERT=float(env.get("READ_LAT_ALERT","300"))
    CPU_WARN=float(env.get("CPU_WARN","70"))
    CPU_ALERT=float(env.get("CPU_ALERT","90"))
    FREE_WARN=float(env.get("FREE_PCT_WARN","20"))
    FREE_ALERT=float(env.get("FREE_PCT_ALERT","10"))

    instances=_list_db_instances(rds)
    rows=[]
    issue_flags=[]  # per-row boolean

    for db in instances:
        dbid=db.get("DBInstanceIdentifier","-")
        engine=(db.get("Engine") or "—").lower()
        public=bool(db.get("PubliclyAccessible",False))
        enc=bool(db.get("StorageEncrypted",False))
        alloc_gb=db.get("AllocatedStorage")
        max_alloc_gb=db.get("MaxAllocatedStorage")
        arn=db.get("DBInstanceArn")
        name_tag=_get_name_tag(rds, arn)

        dims=[{"Name":"DBInstanceIdentifier","Value":dbid}]
        wlat=_cw_latest(cw,"AWS/RDS","WriteLatency",dims,lookback_min,period_sec,"Average")
        rlat=_cw_latest(cw,"AWS/RDS","ReadLatency",dims,lookback_min,period_sec,"Average")
        cpu=_cw_latest(cw,"AWS/RDS","CPUUtilization",dims,lookback_min,period_sec,"Average")
        free_store_b=_cw_latest(cw,"AWS/RDS","FreeStorageSpace",dims,lookback_min,period_sec,"Average")
        conns=_cw_latest(cw,"AWS/RDS","DatabaseConnections",dims,lookback_min,period_sec,"Average")

        write_ms=None if wlat is None else float(wlat)*1000.0
        read_ms=None if rlat is None else float(rlat)*1000.0
        cpu_pct=None if cpu is None else float(cpu)
        free_pct=None
        if free_store_b is not None and alloc_gb:
            total_b=float(alloc_gb)*1024**3
            free_pct=(float(free_store_b)/total_b)*100.0 if total_b>0 else None

        # ----- thresholds for ALERT detection -----
        public_alert = public
        enc_disabled = not enc
        write_alert  = (write_ms is not None and write_ms >= WRITE_ALERT)
        read_alert   = (read_ms  is not None and read_ms  >= READ_ALERT)
        cpu_alert    = (cpu_pct  is not None and cpu_pct  >= CPU_ALERT)
        free_alert   = (free_pct is not None and free_pct <= FREE_ALERT)
        # storage autoscaling enabled = MaxAllocatedStorage > AllocatedStorage
        autoscale_disabled = not (isinstance(alloc_gb, int) and isinstance(max_alloc_gb, int) and max_alloc_gb > alloc_gb)

        row_has_issue = any([public_alert, enc_disabled, write_alert, read_alert, cpu_alert, free_alert, autoscale_disabled])
        issue_flags.append(row_has_issue)

        # ----- formatting cells -----
        pending_has=False
        pending_txt="None"
        try:
            resp=rds.describe_pending_maintenance_actions(ResourceIdentifier=arn)
            acts=resp.get("PendingMaintenanceActions",[])
            if any(item.get("PendingMaintenanceActionDetails") for item in acts):
                pending_has=True
                names=set()
                for item in acts:
                    for det in item.get("PendingMaintenanceActionDetails",[]):
                        nm=det.get("Action") or ""
                        if nm: names.add(nm)
                pending_txt=", ".join(sorted(names)) or "Yes"
        except Exception:
            pass
        pending_lvl = "WARN" if pending_has else "OK"
        pending_cell = f"{_dot_by(pending_lvl)} {pending_txt}"

        autoscale_lvl = "ALERT" if autoscale_disabled else "OK"
        autoscale_cell = f"{_dot_by(autoscale_lvl)} {'Disabled' if autoscale_disabled else 'Enabled'}"

        db_url=_rds_link(region, dbid)
        db_cell_email = f"<a href='{db_url}'>{dbid}</a>" + (f"<br/><a href='{db_url}'>{name_tag}</a>" if name_tag else "")
        db_cell_teams = f"[{dbid}]({db_url})" + (f"\n[{name_tag}]({db_url})" if name_tag else "")

        public_cell = f"{_dot_by('ALERT' if public_alert else 'OK')} {'Yes' if public else 'No'}"
        enc_cell    = f"{_dot_by('ALERT' if enc_disabled else 'OK')} {'Disabled' if enc_disabled else 'Enabled'}"
        write_cell  = f"{_dot_by('ALERT' if write_alert else 'WARN' if (write_ms and write_ms>=WRITE_WARN) else 'OK')} {_fmt_ms(write_ms)}"
        read_cell   = f"{_dot_by('ALERT' if read_alert  else 'WARN' if (read_ms  and read_ms >=READ_WARN) else 'OK')} {_fmt_ms(read_ms)}"
        cpu_cell    = f"{_dot_by('ALERT' if cpu_alert   else 'WARN' if (cpu_pct and cpu_pct>=CPU_WARN) else 'OK')} {_fmt_pct(cpu_pct)}"
        free_cell   = f"{_dot_by('ALERT' if free_alert  else 'WARN' if (free_pct and free_pct<=FREE_WARN) else 'OK')} {_fmt_pct(free_pct)}"
        conns_cell  = f"{int(conns):d}" if isinstance(conns,(int,float)) else "N/A"

        rows.append((db_cell_email, engine, public_cell, enc_cell, write_cell, read_cell,
                     cpu_cell, free_cell, conns_cell, pending_cell, autoscale_cell, db_cell_teams))

    title=f"{acct} - RDS Dashboard (Issues)"
    if not rows:
        return {"ok": True, "instances": 0, "sent": False, "reason": "no-instances"}

    # Keep only failing rows
    failing_rows = [r for r, bad in zip(rows, issue_flags) if bad]
    if not failing_rows:
        return {"ok": True, "instances": len(rows), "sent": False, "reason": "no-issues"}

    # ----- Teams card (only failing rows) -----
    headers=["DB","Engine","Public","Encryption","Write latency","Read latency","CPU","Free space","Connections","Pending Maint","Autoscaling"]
    widths =[6,3,3,3,3,3,3,3,3,3,3]
    body=[{"type":"TextBlock","text":title,"weight":"Bolder","size":"Medium"},
          {"type":"ColumnSet","columns":[_cell(h,bold=True,width=str(widths[i])) for i,h in enumerate(headers)]}]
    for r in failing_rows:
        teams_row = [r[11], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10]]
        body.append({"type":"ColumnSet","columns":[_cell(teams_row[i], width=str(widths[i])) for i in range(len(headers))]})
    card={"type":"message","attachments":[{"contentType":"application/vnd.microsoft.card.adaptive",
            "content":{"$schema":"http://adaptivecards.io/schemas/adaptive-card.json",
                       "type":"AdaptiveCard","version":"1.4","body":body}}]}

    if webhook or os.environ.get("TEAMS_WEBHOOK",""):
        post_to_teams(webhook or os.environ.get("TEAMS_WEBHOOK",""), card)

    # ----- Email (only failing rows) -----
    html, txt = _build_email_html(title, failing_rows)
    _send_email_ses(session, region, title, html, txt, env)

    return {"ok": True, "instances": len(rows), "sent": True, "issues": len(failing_rows)}
