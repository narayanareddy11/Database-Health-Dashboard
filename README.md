# Database Health Dashboard – Automated RDS Monitoring

Automated **RDS health, performance, and configuration audit** for AWS environments.  
Runs as an **AWS Lambda function**, collects enhanced RDS metrics, and posts results to **Microsoft Teams** and **SES email**.

---

## What this does

Each run evaluates Amazon RDS instances against best practices.  
Only WARN/ALERT findings are reported (healthy checks are suppressed).

Checks include:
- **Connectivity & Security**: Publicly accessible flag, Encryption status, Pending maintenance.
- **Performance Metrics**: Write/Read latency, CPU utilization, Free storage, Active connections.
- **Configuration**: Autoscaling enabled/disabled, Multi-AZ, Backup/Retention.

---

## Repository layout

```
Database_Health_Dashboard/
├─ app/
│  ├─ __init__.py
│  └─ main.py                 # Lambda entrypoint, orchestrates RDS health checks
├─ shared/
│  ├─ __init__.py
│  ├─ teams.py                # MS Teams webhook integration
│  ├─ collectors.py           # Helper to fetch AWS account label via STS
│  └─ email copy.py           # SES email integration
├─ rds_enhanced/
│  ├─ __init__.py
│  └─ handler.py              # RDS audit checks & metrics collection
```

---

## Runtime architecture

```
┌──────────────┐
│   Lambda     │
│  (app.main)  │
└─────┬────────┘
      │
      ▼
┌──────────────┐
│ rds_enhanced │  (handler.py executes all RDS checks)
└─────┬────────┘
      │ aggregated results
      ▼
┌──────────────┐
│ MS Teams     │ (Adaptive Cards – WARN/ALERT only)
└──────────────┘
      │
      ▼
┌──────────────┐
│ AWS SES      │ (HTML Email report)
└──────────────┘
```

---

## Lambda handler

- **Module**: `app.main`
- **Function**: `lambda_handler`
- Collects all RDS DB instances → evaluates metrics → builds Teams card + Email → posts only issues.

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `AWS_REGION` | `us-east-1` | AWS region |
| `TEAMS_WEBHOOK` | **required** | Teams Incoming Webhook URL |
| `ENABLE_MAIL_REPORT` | `true` | Master toggle for email |
| `MAIL_FROM` | *(empty)* | Verified SES sender |
| `MAIL_TO` | *(empty)* | Recipients (comma/semicolon) |
| `MAIL_CC` / `MAIL_BCC` | *(empty)* | Optional CC/BCC recipients |
| `MAIL_SUBJECT` | `AWS RDS Health Dashboard Report` | Subject override |
| `LOG_LEVEL` | `INFO` | Logging level |

---

## IAM permissions

Attach a Lambda execution role with:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": ["rds:DescribeDBInstances","rds:DescribeDBClusters"], "Resource": "*"},
    {"Effect": "Allow", "Action": ["cloudwatch:GetMetricStatistics","cloudwatch:ListMetrics"], "Resource": "*"},
    {"Effect": "Allow", "Action": ["ses:SendEmail","ses:SendRawEmail"], "Resource": "*"},
    {"Effect": "Allow", "Action": ["sts:GetCallerIdentity"], "Resource": "*"}
  ]
}
```

---

## Deployment (AWS Lambda)

1. **Package** into a ZIP (root must contain `app/`, `shared/`, `rds_enhanced/`).  
2. **Create Lambda**:
   - Runtime: Python 3.11+
   - Handler: `app.main.lambda_handler`
   - Memory/Timeout: 512–1024 MB, 1–3 min  
3. **Environment variables**: configure from above table.  
4. **Execution role**: attach IAM permissions.  
5. **Trigger**: EventBridge scheduled rule (e.g., `rate(1 day)` or weekly).

---

## Sample Teams Output

### RDS Dashboard (Issues)

| DB         | Engine   | Public | Encryption | Write latency | Read latency | CPU | Free space | Connections | Pending Maint | Autoscaling |
|------------|----------|--------|------------|---------------|--------------|-----|------------|-------------|---------------|-------------|
| database-1 | postgres | 🔴 Yes | 🟢 Enabled | 🟢 1 ms       | 🟢 0 ms      | 🟢 4% | 🟢 80%    | 0           | 🟢 None       | 🔴 Disabled |

Legend:
- **🟢** Healthy  
- **🟡** Warning (approaching threshold)  
- **🔴** Alert (critical issue)  

---
## Environment variables

| Variable          | Default | Description |
|-------------------|---------|-------------|
| `AWS_REGION`      | us-east-1 | AWS region |
| `TEAMS_WEBHOOK`   | (required) | Teams Incoming Webhook URL |
| `ENABLE_MAIL_REPORT` | true | Enable/disable SES email reporting |
| `MAIL_FROM`       | xx-reply@.com | Verified SES sender |
| `MAIL_TO`         | xxxx@x.com | Comma/semicolon-separated recipients |
| `MAIL_CC` / `MAIL_BCC` | *(empty)* | Optional CC/BCC |
| `MAIL_SUBJECT`    | Database Utilization Report | Email subject line |
| `CPU_WARN`        | 80 | CPU utilization warning threshold (%) |
| `CPU_ALERT`       | 90 | CPU utilization alert threshold (%) |
| `FREE_PCT_WARN`   | 20 | Free storage warning threshold (%) |
| `FREE_PCT_ALERT`  | 10 | Free storage alert threshold (%) |
| `READ_LAT_WARN`   | 200 | Read latency warning (ms) |
| `READ_LAT_ALERT`  | 300 | Read latency alert (ms) |
| `WRITE_LAT_WARN`  | 200 | Write latency warning (ms) |
| `WRITE_LAT_ALERT` | 300 | Write latency alert (ms) |
| `LOG_LEVEL`       | INFO | Logging level |

## Email Output (SES)

- HTML table similar to Teams Adaptive Card.  
- Sent only if `ENABLE_MAIL_REPORT=true`.

---

## Troubleshooting

- **No DB instances found** → check account/region permissions.  
- **No Teams alerts** → verify `TEAMS_WEBHOOK`.  
- **No Email** → check SES sandbox/verified addresses.  
- **Missing metrics** → ensure RDS Enhanced Monitoring or CloudWatch metrics enabled.

---

## Roadmap

- Add **RDS storage autoscaling** monitoring.  
- Add **Multi-AZ & backup compliance** checks.  
- Add **Performance Insights** anomalies.  
- Export historical reports to **S3**.


