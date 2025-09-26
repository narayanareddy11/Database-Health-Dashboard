
def send_email(session, region, subject, text_body, html_body, env):
    ses = session.client("ses", region_name=region)
    from_addr = env.get("SES_FROM")
    to_addr   = env.get("SES_TO")
    if not from_addr or not to_addr:
        return {"ok": False, "error": "SES_FROM or SES_TO not set"}
    ses.send_email(
        Source=from_addr,
        Destination={"ToAddresses": [to_addr]},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {
                "Text": {"Data": text_body, "Charset": "UTF-8"},
                "Html": {"Data": html_body, "Charset": "UTF-8"}
            }
        }
    )
    return {"ok": True}