#!/usr/bin/env python3
"""Read-only AWS cost and tagging collector. Stdlib plus the aws CLI.

Reads last month's bill from Cost Explorer, then scans every region that had
spend for untagged, idle and mispriced resources. Writes facts.json. Every
dollar figure in it comes from the bill, or is labelled as a list price or an
upper bound. Only describe/list/get calls can be made, see policy.json.

Usage: python3 collect.py [--profile P] [--tags environment,project,owner]
                          [--regions us-east-1,eu-west-1] [--out facts.json]
       python3 collect.py --selftest

Cost Explorer bills $0.01 per request. A run makes about 4, plus one per
required tag that is active.
"""
import argparse
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

HOURS = 730  # hours in the billing month; collect() sets the real value so findings reconcile with the bill
# us-east-1 list prices in USD. Fallback only: used when the bill has no matching line.
LIST = {"gp2": 0.10, "gp3": 0.08, "io1": 0.125, "io2": 0.125, "st1": 0.045, "sc1": 0.015, "standard": 0.05,
        "snapshot": 0.05, "ipv4_hr": 0.005, "lb_hr": 0.0225, "logs_gb": 0.03, "eks_extended_hr": 0.50}
EBS_USAGE = {"gp2": "EBS:VolumeUsage.gp2", "gp3": "EBS:VolumeUsage.gp3", "io1": "EBS:VolumeUsage.piops",
             "io2": "EBS:VolumeUsage.io2", "st1": "EBS:VolumeUsage.st1", "sc1": "EBS:VolumeUsage.sc1",
             "standard": "EBS:VolumeUsage"}
BASE_FILTER = {"Not": {"Dimensions": {"Key": "RECORD_TYPE", "Values": ["Credit", "Refund", "Tax"]}}}
# First match wins: a tag key prefix on the resource says what created it. SKILL.md maps each creator to where its tags are set.
ORIGINS = [
    ("karpenter.sh/", "Karpenter"),
    ("eks:nodegroup-name", "EKS managed node group"),
    ("aws:autoscaling:groupName", "Auto Scaling group"),
    ("ebs.csi.aws.com/", "EBS CSI driver"),
    ("kubernetes.io/created-for/pvc", "EBS CSI driver"),
    ("elbv2.k8s.aws/cluster", "AWS Load Balancer Controller"),
    ("kubernetes.io/service-name", "Kubernetes service controller"),
    ("aws:cloudformation:stack-name", "CloudFormation"),
]
DIRECT = "no controller (Terraform, console or CLI)"
VOLUME = "the instance it is attached to"

PROFILE = None
CALLS, ERRORS = set(), []
POOL = ThreadPoolExecutor(8)


class AwsError(Exception):
    pass


def cli(service, op, args, region):
    cmd = ["aws", service, op, *args, "--output", "json", "--no-cli-pager"]
    cmd += ["--region", region] if region else []
    cmd += ["--profile", PROFILE] if PROFILE else []
    try:
        p = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        raise AwsError("aws CLI not found on PATH")
    if p.returncode:
        raise AwsError((p.stderr.strip().splitlines() or [f"exit {p.returncode}"])[-1])
    return json.loads(p.stdout or "{}")


RUN = cli  # swapped for fixtures by selftest


def aws(service, op, *args, region=None):
    if not op.startswith(("describe-", "list-", "get-")):
        raise ValueError(f"refusing non-read call: {service} {op}")
    CALLS.add((service, op))
    return RUN(service, op, args, region)


def safe(service, op, *args, region=None):
    """Response, or None when the call failed. The failure is recorded, never guessed around."""
    try:
        return aws(service, op, *args, region=region)
    except AwsError as e:
        ERRORS.append({"call": f"{region or 'global'} {service} {op}", "error": str(e)})
        return None


def ce_groups(period, group_by):
    """[(keys, usd, quantity, unit)] for one month of Cost Explorer, following pagination.

    Amortized cost, so a Savings Plan or reservation fee is spread over the usage it covers. Unblended cost
    would show that usage as free and the fee as one line that no tag can ever reach.
    """
    args = ["--time-period", f"Start={period[0]},End={period[1]}", "--granularity", "MONTHLY",
            "--metrics", "AmortizedCost", "UsageQuantity", "--filter", json.dumps(BASE_FILTER), "--group-by", *group_by]
    rows, token = [], None
    while True:
        # ponytail: us-east-1 is the commercial partition's Cost Explorer endpoint, GovCloud and China need their own
        page = aws("ce", "get-cost-and-usage", *args, *(["--next-page-token", token] if token else []), region="us-east-1")
        for result in page.get("ResultsByTime", []):
            for g in result.get("Groups", []):
                m = g["Metrics"]
                rows.append((g["Keys"], float(m["AmortizedCost"]["Amount"]), float(m["UsageQuantity"]["Amount"]), m["UsageQuantity"].get("Unit", "")))
        token = page.get("NextPageToken")
        if not token:
            return rows


def bill(lines, suffix, service=""):
    """(usd, quantity) summed over bill lines whose usage type ends with suffix. Region prefixes vary, suffixes do not."""
    hit = [l for l in lines if l["usage_type"].endswith(suffix) and service in l["service"]]
    return sum(l["usd"] for l in hit), sum(l["quantity"] for l in hit)


def rate(lines, suffix, fallback, service=""):
    usd, qty = bill(lines, suffix, service)
    return (usd / qty, "bill rate") if usd > 0 and qty > 0 else (fallback, "list price")


def capped(estimate, lines, suffix, service=""):
    """An estimate can never exceed what the account actually paid for that usage type."""
    usd, _ = bill(lines, suffix, service)
    return min(estimate, usd) if usd > 0 else estimate


def tagdict(tags):
    return tags if isinstance(tags, dict) else {t["Key"]: t.get("Value", "") for t in tags or []}


def origin(tags):
    return next((who for prefix, who in ORIGINS if any(k.startswith(prefix) for k in tagdict(tags))), DIRECT)


def count(n, plural):
    return f"{n} {plural if n != 1 else plural[:-1]}"


def finding(check, region, resource, detail, usd=None, basis=None, evidence="", resources=None, **extra):
    """region None means account-wide. resources lists at most 50 ids; the real count is in resource."""
    f = {"check": check, "region": region, "resource": resource, "detail": detail,
         "monthly_usd": None if usd is None else round(usd, 2), "basis": basis, "evidence": evidence, **extra}
    if resources:
        f["resources"] = resources[:50]
    return f


def scan_region(region, lines, required, today):
    out, untagged, seen, nats = [], defaultdict(list), defaultdict(int), []

    def get(service, op, key, *args):
        """List under key, or None when the call failed so that dependent checks are skipped."""
        resp = safe(service, op, *args, region=region)
        return None if resp is None else resp.get(key, [])

    def add(*args, **kw):
        out.append(finding(args[0], region, *args[1:], **kw))

    wanted = {t.lower() for t in required}

    def check_tags(kind, rid, tags, hint=None):
        tags = tagdict(tags)
        have = {k.lower() for k in tags}
        for k in tags:
            if k.lower() in wanted:
                seen[k] += 1
        gone = tuple(t for t in required if t.lower() not in have)
        if gone:
            who = origin(tags)
            untagged[kind, hint if hint and who == DIRECT else who, gone].append(rid)

    # EBS volumes
    def vol_creator(v):
        who = origin(v.get("Tags"))
        return VOLUME if who == DIRECT and v.get("Attachments") else who

    vols = get("ec2", "describe-volumes", "Volumes") or []
    for v in vols:
        check_tags("EBS volumes", v["VolumeId"], v.get("Tags"), VOLUME if v.get("Attachments") else None)
        if v["State"] == "available":
            r, basis = rate(lines, EBS_USAGE.get(v["VolumeType"], "?"), LIST.get(v["VolumeType"], LIST["gp3"]))
            add("ebs-unattached", v["VolumeId"], f"{v['Size']} GiB {v['VolumeType']}, created {str(v['CreateTime'])[:10]}",
                usd=v["Size"] * r, basis=basis, evidence="aws ec2 describe-volumes --filters Name=status,Values=available",
                created_by=vol_creator(v))
    gp2 = [v for v in vols if v["VolumeType"] == "gp2" and v["State"] != "available"]
    if gp2:
        gib, big = sum(v["Size"] for v in gp2), sum(v["Size"] > 1000 for v in gp2)
        r, basis = rate(lines, EBS_USAGE["gp2"], LIST["gp2"])
        add("ebs-gp2", count(len(gp2), "volumes"), f"{gib} GiB on gp2; {big} over 1000 GiB need extra gp3 IOPS to match",
            usd=gib * r * 0.2, basis=f"{basis}, gp3 costs 20% less per GiB",
            evidence="aws ec2 describe-volumes --filters Name=volume-type,Values=gp2", resources=[v["VolumeId"] for v in gp2],
            volumes_by_creator=dict(Counter(vol_creator(v) for v in gp2)))

    # Elastic IPs
    for a in get("ec2", "describe-addresses", "Addresses") or []:
        if "AssociationId" not in a:
            r, basis = rate(lines, "PublicIPv4:IdleAddress", LIST["ipv4_hr"])
            add("eip-unassociated", a.get("AllocationId") or a.get("PublicIp"), "Elastic IP not associated with anything",
                usd=r * HOURS, basis=basis, evidence="aws ec2 describe-addresses --query 'Addresses[?!AssociationId]'")

    # Snapshots. Ones that back a registered AMI cannot be deleted, so they are counted but not flagged.
    images = get("ec2", "describe-images", "Images", "--owners", "self")
    snaps = get("ec2", "describe-snapshots", "Snapshots", "--owner-ids", "self")
    if images is not None and snaps:
        ami = {b["Ebs"]["SnapshotId"] for i in images for b in i.get("BlockDeviceMappings", []) if b.get("Ebs", {}).get("SnapshotId")}
        old = [s for s in snaps if str(s["StartTime"])[:10] < str(today - timedelta(days=90))]
        loose = [s for s in old if s["SnapshotId"] not in ami]
        if loose:
            gib = sum(s["VolumeSize"] for s in loose)
            r, basis = rate(lines, "EBS:SnapshotUsage", LIST["snapshot"])
            add("snapshot-old", count(len(loose), "snapshots"),
                f"older than 90 days, {gib} GiB of source volumes, oldest from {min(str(s['StartTime'])[:10] for s in loose)}; "
                f"{len(old) - len(loose)} more back an AMI and were left out",
                usd=capped(gib * r, lines, "EBS:SnapshotUsage"),
                basis=f"upper bound: full volume size at {basis}, snapshots are incremental",
                evidence="aws ec2 describe-snapshots --owner-ids self", resources=[s["SnapshotId"] for s in loose])

    # NAT gateways in a VPC with no S3 gateway endpoint
    nat_vpcs = defaultdict(list)
    for n in get("ec2", "describe-nat-gateways", "NatGateways") or []:
        if n.get("State") == "available":
            nat_vpcs[n["VpcId"]].append(n["NatGatewayId"])
            nats.append(f"{n['NatGatewayId']} ({n['VpcId']}, {region})")
            check_tags("NAT gateways", n["NatGatewayId"], n.get("Tags"))
    endpoints = get("ec2", "describe-vpc-endpoints", "VpcEndpoints") if nat_vpcs else None
    if endpoints is not None:
        s3 = {e["VpcId"] for e in endpoints if e.get("VpcEndpointType") == "Gateway"
              and e.get("ServiceName", "").endswith(".s3") and e.get("State") == "available"}
        usd, gb = bill(lines, "NatGateway-Bytes")
        for vpc, ids in nat_vpcs.items():
            if vpc not in s3:
                add("nat-no-s3-endpoint", vpc,
                    f"{count(len(ids), 'NAT gateways')} and no S3 gateway endpoint, so S3 and ECR image layer traffic pays NAT data "
                    f"processing; account-wide NAT processing last month was ${usd:,.2f} for {gb:,.0f} GB",
                    basis="not estimated: the S3 share of NAT traffic needs VPC flow logs",
                    evidence=f"aws ec2 describe-vpc-endpoints --filters Name=vpc-id,Values={vpc}")

    # EC2 instances (tags only)
    for res in get("ec2", "describe-instances", "Reservations") or []:
        for i in res["Instances"]:
            if i["State"]["Name"] not in ("terminated", "shutting-down"):
                check_tags("EC2 instances", i["InstanceId"], i.get("Tags"))

    # Load balancers with no registered targets
    def tg_targets(tg):
        try:
            resp = aws("elbv2", "describe-target-health", "--target-group-arn", tg["TargetGroupArn"], region=region)
            return len(resp.get("TargetHealthDescriptions", []))
        except AwsError as e:
            ERRORS.append({"call": f"{region} elbv2 describe-target-health", "error": str(e)})
            return 1  # unknown, so the load balancer is not flagged

    r, basis = rate(lines, "LoadBalancerUsage", LIST["lb_hr"])
    lbs = get("elbv2", "describe-load-balancers", "LoadBalancers") or []
    arns, lb_tags = [lb["LoadBalancerArn"] for lb in lbs], {}
    for i in range(0, len(arns), 20):  # describe-tags takes 20 ARNs at most
        for d in get("elbv2", "describe-tags", "TagDescriptions", "--resource-arns", *arns[i:i + 20]) or []:
            lb_tags[d["ResourceArn"]] = tagdict(d.get("Tags"))
            check_tags("load balancers", d["ResourceArn"].split("/")[-2], lb_tags[d["ResourceArn"]])
    tgs = get("elbv2", "describe-target-groups", "TargetGroups") if lbs else None
    if tgs is not None:
        tgs = [t for t in tgs if t.get("LoadBalancerArns")]
        targets = defaultdict(int)
        for tg, n in zip(tgs, POOL.map(tg_targets, tgs)):
            for arn in tg["LoadBalancerArns"]:
                targets[arn] += n
        for lb in lbs:
            if not targets[lb["LoadBalancerArn"]]:
                add("lb-no-targets", lb["LoadBalancerName"], f"{lb.get('Type', 'application')} load balancer with no registered targets",
                    usd=r * HOURS, basis=basis, evidence="aws elbv2 describe-target-health --target-group-arn ARN (each target group)",
                    created_by=origin(lb_tags.get(lb["LoadBalancerArn"])))
    for lb in get("elb", "describe-load-balancers", "LoadBalancerDescriptions") or []:
        if not lb.get("Instances"):
            add("lb-no-targets", lb["LoadBalancerName"], "classic load balancer with no instances",
                usd=r * HOURS, basis=basis, evidence="aws elb describe-load-balancers")

    # Log groups that never expire
    forever = [g for g in get("logs", "describe-log-groups", "logGroups") or [] if "retentionInDays" not in g]
    if forever:
        gib = sum(g.get("storedBytes", 0) for g in forever) / 2**30
        r, basis = rate(lines, "TimedStorage-ByteHrs", LIST["logs_gb"], "CloudWatch")
        forever.sort(key=lambda g: -g.get("storedBytes", 0))
        add("log-no-retention", count(len(forever), "log groups"), f"never expire, {gib:.0f} GiB stored" if gib >= 10 else f"never expire, {gib:.1f} GiB stored",
            usd=capped(gib * r, lines, "TimedStorage-ByteHrs", "CloudWatch"),
            basis=f"upper bound: current storage at {basis}; the saving depends on the retention chosen",
            evidence="aws logs describe-log-groups --query 'logGroups[?!retentionInDays]'",
            resources=[g["logGroupName"] for g in forever],
            largest=[{"name": g["logGroupName"], "gib": round(g.get("storedBytes", 0) / 2**30, 1)} for g in forever[:5]])

    # EKS clusters in, or close to, extended support
    names = get("eks", "list-clusters", "clusters") or []
    versions = {v["clusterVersion"]: v for v in get("eks", "describe-cluster-versions", "clusterVersions") or []} if names else {}
    for name in names:
        c = (safe("eks", "describe-cluster", "--name", name, region=region) or {}).get("cluster")
        if not c:
            continue
        check_tags("EKS clusters", name, c.get("tags"))
        v = versions.get(c["version"], {})
        status = str(v.get("versionStatus") or v.get("status") or "").lower()
        ends = str(v.get("endOfStandardSupportDate") or "")[:10]
        extra = LIST["eks_extended_hr"] * HOURS
        if "extended" in status or (ends and ends <= str(today)):
            add("eks-extended-support", name, f"Kubernetes {c['version']} left standard support on {ends or 'an unknown date'}",
                usd=extra, basis="list price: $0.60 per hour instead of $0.10", evidence="aws eks describe-cluster-versions")
        elif ends and ends < str(today + timedelta(days=90)):
            add("eks-extended-soon", name, f"Kubernetes {c['version']} leaves standard support on {ends}; extended support adds ${extra:.0f} a month",
                basis="starts on that date unless the cluster is upgraded", evidence="aws eks describe-cluster-versions",
                starts=ends, then_usd=round(extra, 2))

    # RDS (tags, plus an inventory for the extended support line on the bill)
    dbs = get("rds", "describe-db-instances", "DBInstances") or []
    for d in dbs:
        check_tags("RDS instances", d["DBInstanceIdentifier"], d.get("TagList"))

    for (kind, who, gone), ids in untagged.items():
        add("tag-missing", count(len(ids), kind), f"missing {', '.join(gone)}; created by {who}", resources=ids,
            missing=list(gone), created_by=who)
    return out, [f"{d['DBInstanceIdentifier']} ({d['Engine']} {d['EngineVersion']}, {region})" for d in dbs], seen, nats


def tag_report(required, period, total, seen, findings):
    """Per required tag: can Cost Explorer group by it, and how much spend carries no value for it.

    Works from a linked account: it asks Cost Explorer which tag keys it has data for, not the payer-only
    cost allocation tag list. seen = {spelling: count} of the required keys found on scanned resources.
    """
    resp = safe("ce", "get-tags", "--time-period", f"Start={period[0]},End={period[1]}", region="us-east-1")
    in_ce = None if resp is None else set(resp.get("Tags", []))  # None: could not ask, so try the spellings on resources
    report = {}
    for want in required:
        on_resources = sorted(k for k in seen if k.lower() == want.lower())
        candidates = on_resources or [want] if in_ce is None else sorted(k for k in in_ce if k.lower() == want.lower())
        best = None
        for key in candidates:
            rows = ce_groups(period, [f"Type=TAG,Key={key}", "Type=DIMENSION,Key=SERVICE"])
            if sum(usd for k, usd, *_ in rows if k[0] != key + "$") <= 0:
                continue  # no spend carries a value for it, so Cost Explorer cannot group by it
            by_service = {k[1]: usd for k, usd, *_ in rows if k[0] == key + "$" and usd > 0}  # "Key$" is the no-value bucket
            usd = sum(by_service.values())
            if best is None or usd < best["untagged_usd"]:
                top = sorted(by_service.items(), key=lambda kv: -kv[1])[:8]
                best = {"key": key, "untagged_usd": round(usd, 2), "untagged_pct": round(100 * usd / total, 1) if total else None,
                        "untagged_by_service": {k: round(v, 2) for k, v in top}}
        report[want] = best or {"key": None, "untagged_usd": None, "untagged_pct": None, "untagged_by_service": {}}
        variants = sorted(set(on_resources) | set(candidates if in_ce is not None else []))
        if len(variants) > 1:
            findings.append(finding("tag-case-variants", None, ", ".join(variants),
                                    "tag keys are case-sensitive, so each spelling is a separate tag and the spend splits between them",
                                    on_resources={k: seen.get(k, 0) for k in variants}))
        if best is None and on_resources:
            n = sum(seen[k] for k in on_resources)
            findings.append(finding("tag-not-activated", None, ", ".join(on_resources),
                                    f"on {n} scanned resources, but Cost Explorer has no spend under it: it is not activated as a "
                                    "cost allocation tag in the payer account", evidence="aws ce get-tags --time-period Start=..,End=.."))
        elif best is None:
            findings.append(finding("tag-not-used", None, want, "not on any scanned resource and not in Cost Explorer",
                                    evidence="aws ce get-tags --time-period Start=..,End=.."))
    return report


def nat_idle(lines, nats):
    """NAT gateways billed by the hour while almost nothing went through them. nats = the gateways found by the scan."""
    usd, hours = bill(lines, "NatGateway-Hours")
    _, gb = bill(lines, "NatGateway-Bytes")
    # ponytail: bill totals only, so an idle gateway next to a busy one is missed; per gateway needs CloudWatch BytesOutToDestination
    if hours > 0 and gb / (hours / HOURS) < 1:
        return [finding("nat-idle", None, "NAT gateways", f"{hours:.0f} NAT gateway hours billed and {gb:.2f} GB processed in the whole month",
                        usd=usd, basis="bill", evidence="Cost Explorer usage types NatGateway-Hours and NatGateway-Bytes", resources=nats)]
    return []


def collect(required, regions=None, today=None):
    global HOURS
    today = today or date.today()
    end = today.replace(day=1)
    start = (end - timedelta(days=1)).replace(day=1)
    period, HOURS = (str(start), str(end)), (end - start).days * 24
    ident = aws("sts", "get-caller-identity", region="us-east-1")

    lines, spend = [], {}
    try:
        rows = ce_groups(period, ["Type=DIMENSION,Key=SERVICE", "Type=DIMENSION,Key=USAGE_TYPE"])
        lines = [{"service": k[0], "usage_type": k[1], "usd": usd, "quantity": qty, "unit": unit} for k, usd, qty, unit in rows]
        spend = {k[0]: round(usd, 2) for k, usd, *_ in ce_groups(period, ["Type=DIMENSION,Key=REGION"])}
    except AwsError as e:
        ERRORS.append({"call": "global ce get-cost-and-usage", "error": str(e)})
    total = sum(l["usd"] for l in lines)
    if not regions:
        regions = sorted(k for k, usd in spend.items() if usd > 0 and re.fullmatch(r"[a-z]{2}(-[a-z]+)+-\d", k))
    if not regions:
        sys.exit("no bill to pick regions from (Cost Explorer denied or empty): pass --regions")

    findings, tags, rds, nats, seen = [], {}, [], [], defaultdict(int)
    for region in regions:  # ponytail: regions run one after another, pool them if a many-region account is slow
        found, dbs, spellings, gateways = scan_region(region, lines, required, today)
        findings += found
        rds += dbs
        nats += gateways
        for k, n in spellings.items():
            seen[k] += n
    findings += nat_idle(lines, nats)
    try:
        tags = tag_report(required, period, total, seen, findings)
    except AwsError as e:
        ERRORS.append({"call": "global ce get-cost-and-usage by tag", "error": str(e)})
    for l in lines:
        if "extendedsupport" in l["usage_type"].lower() and "Relational" in l["service"]:
            findings.append(finding("rds-extended-support", None, l["usage_type"],
                                    f"RDS extended support charge last month; instances found: {', '.join(rds) or 'none'}",
                                    usd=l["usd"], basis="bill", evidence="Cost Explorer grouped by usage type"))

    findings.sort(key=lambda f: (f["monthly_usd"] is None, -(f["monthly_usd"] or 0)))
    priced = [f for f in findings if f["monthly_usd"]]
    by_check = {}
    for f in findings:
        row = by_check.setdefault(f["check"], {"check": f["check"], "findings": 0, "monthly_usd": None, "kind": "not priced"})
        row["findings"] += 1
        if f["monthly_usd"] is not None:
            row["monthly_usd"] = round((row["monthly_usd"] or 0) + f["monthly_usd"], 2)
            row["kind"] = "ceiling" if f["basis"].startswith("upper bound") else "saving"
    identified = sum(f["monthly_usd"] for f in priced if not f["basis"].startswith("upper bound"))
    ceiling = sum(f["monthly_usd"] for f in priced if f["basis"].startswith("upper bound"))

    def pct(usd):
        return round(100 * usd / total, 1) if total else None
    top = sorted(lines, key=lambda l: -l["usd"])[:40]
    return {
        "account": ident.get("Account"), "collected": str(today), "period": {"start": period[0], "end": period[1]},
        "total_usd": round(total, 2), "spend_by_region": spend, "regions_scanned": regions, "required_tags": required, "tags": tags,
        "identified_usd": round(identified, 2), "identified_pct": pct(identified),
        "upper_bound_usd": round(ceiling, 2), "upper_bound_pct": pct(ceiling),
        "upcoming_usd": round(sum(f.get("then_usd", 0) for f in findings), 2),
        "by_check": sorted(by_check.values(), key=lambda r: -(r["monthly_usd"] or 0)),
        "findings": findings,
        "bill_top_pct": pct(sum(l["usd"] for l in top)),
        "bill_top": [{"service": l["service"], "usage_type": l["usage_type"], "usd": round(l["usd"], 2), "pct": pct(l["usd"]),
                      "quantity": round(l["quantity"], 2), "unit": l["unit"],
                      "rate": float(f"{l['usd'] / l['quantity']:.4g}") if l["quantity"] else None} for l in top],
        "errors": ERRORS,
    }


def summary(facts):
    """Safe to paste anywhere: no account ids, resource names or dollar amounts."""
    counts = defaultdict(int)
    for f in facts["findings"]:
        counts[f["check"]] += 1
    out = [f"period {facts['period']['start']} to {facts['period']['end']}, regions scanned: {', '.join(facts['regions_scanned'])}",
           "bill: " + ("read from Cost Explorer" if facts["bill_top"] else "not available, list prices used"),
           "tags: " + (", ".join(f"{k} {'active' if v['key'] else 'not usable'}" for k, v in facts["tags"].items()) or "not checked"),
           "findings: " + (", ".join(f"{k} {n}" for k, n in sorted(counts.items())) or "none")]
    for e in facts["errors"]:
        code = re.search(r"\((\w+)\)", e["error"])
        out.append(f"error: {e['call']} ({code.group(1) if code else 'failed'})")
    return "\n".join(out)


def selftest():
    global RUN

    def groups(*rows):
        return {"ResultsByTime": [{"Groups": [{"Keys": k, "Metrics": {"AmortizedCost": {"Amount": str(usd)}, "UsageQuantity": {"Amount": str(qty)}}}
                                               for k, usd, qty in rows]}]}

    lb = "arn:aws:elasticloadbalancing:us-east-1:111122223333:loadbalancer/app/idle/abc"
    fixtures = {
        ("sts", "get-caller-identity"): {"Account": "111122223333"},
        ("ce", "get-tags"): {"Tags": ["Environment", "aws:createdBy"]},
        ("ec2", "describe-volumes"): {"Volumes": [
            {"VolumeId": "vol-loose", "State": "available", "VolumeType": "gp2", "Size": 100, "CreateTime": "2025-01-01T00:00:00+00:00",
             "Tags": [{"Key": "Environment", "Value": "dev"}, {"Key": "Project", "Value": "x"}]},
            {"VolumeId": "vol-pvc", "State": "in-use", "VolumeType": "gp2", "Size": 500, "CreateTime": "2025-01-01T00:00:00+00:00",
             "Attachments": [{}], "Tags": [{"Key": "ebs.csi.aws.com/cluster", "Value": "true"}]}]},
        ("ec2", "describe-addresses"): {"Addresses": [{"AllocationId": "eipalloc-idle"}, {"AllocationId": "eipalloc-used", "AssociationId": "a"}]},
        ("ec2", "describe-images"): {"Images": [{"BlockDeviceMappings": [{"Ebs": {"SnapshotId": "snap-ami"}}]}]},
        ("ec2", "describe-snapshots"): {"Snapshots": [
            {"SnapshotId": "snap-old", "StartTime": "2024-01-01T00:00:00+00:00", "VolumeSize": 100},
            {"SnapshotId": "snap-ami", "StartTime": "2024-01-01T00:00:00+00:00", "VolumeSize": 100},
            {"SnapshotId": "snap-new", "StartTime": "2026-11-01T00:00:00+00:00", "VolumeSize": 100}]},
        ("ec2", "describe-nat-gateways"): {"NatGateways": [{"NatGatewayId": "nat-1", "VpcId": "vpc-1", "State": "available", "Tags": [{"Key": "environment", "Value": "dev"}]}]},
        ("ec2", "describe-vpc-endpoints"): {"VpcEndpoints": [{"VpcId": "vpc-1", "VpcEndpointType": "Interface", "ServiceName": "com.amazonaws.us-east-1.s3", "State": "available"}]},
        ("ec2", "describe-instances"): {"Reservations": [{"Instances": [
            {"InstanceId": "i-node", "State": {"Name": "running"}, "Tags": [{"Key": "karpenter.sh/nodepool", "Value": "default"}]},
            {"InstanceId": "i-gone", "State": {"Name": "terminated"}}]}]},
        ("elbv2", "describe-load-balancers"): {"LoadBalancers": [{"LoadBalancerArn": lb, "LoadBalancerName": "idle", "Type": "application"}]},
        ("elbv2", "describe-target-groups"): {"TargetGroups": [{"TargetGroupArn": "tg", "LoadBalancerArns": [lb]}]},
        ("elbv2", "describe-target-health"): {"TargetHealthDescriptions": []},
        ("logs", "describe-log-groups"): {"logGroups": [{"logGroupName": "/forever", "storedBytes": 2**30}, {"logGroupName": "/ok", "retentionInDays": 30}]},
        ("eks", "list-clusters"): {"clusters": ["old"]},
        ("eks", "describe-cluster-versions"): {"clusterVersions": [{"clusterVersion": "1.29", "versionStatus": "EXTENDED_SUPPORT", "endOfStandardSupportDate": "2025-03-23T00:00:00+00:00"}]},
        ("eks", "describe-cluster"): {"cluster": {"name": "old", "version": "1.29", "tags": {}}},
    }

    def fake(service, op, args, region):
        if (service, op) != ("ce", "get-cost-and-usage"):
            return fixtures.get((service, op), {})
        if "Type=DIMENSION,Key=REGION" in args:
            return groups((["us-east-1"], 900, 0), (["global"], 5, 0), (["NoRegion"], 1, 0))
        if "Type=TAG,Key=Environment" in args:
            return groups((["Environment$", "EC2 - Other"], 300, 0), (["Environment$prod", "EC2 - Other"], 600, 0))
        if "--next-page-token" in args:
            return groups((["Amazon Virtual Private Cloud", "USE1-PublicIPv4:IdleAddress"], 7.3, 1460),
                          (["Amazon Relational Database Service", "USE1-ExtendedSupport:Yr1-Yr2:MySQL5.7"], 100, 1))
        page = groups((["EC2 - Other", "EBS:VolumeUsage.gp2"], 60, 600), (["EC2 - Other", "NatGateway-Bytes"], 45, 1000),
                      (["Amazon Elastic Compute Cloud - Compute", "BoxUsage:m5.large"], 654.85, 720), (["EC2 - Other", "NatGateway-Hours"], 32.85, 730))
        return {**page, "NextPageToken": "p2"}

    RUN = fake
    facts = collect(["environment", "project", "owner"], today=date(2026, 11, 12))
    by = {f["check"]: f for f in facts["findings"] if f["check"] != "tag-missing"}
    assert facts["period"] == {"start": "2026-10-01", "end": "2026-11-01"} and facts["regions_scanned"] == ["us-east-1"], facts
    assert facts["total_usd"] == 900.0, "second bill page was not followed"
    assert (by["ebs-unattached"]["monthly_usd"], by["ebs-unattached"]["basis"]) == (10.0, "bill rate"), by["ebs-unattached"]
    assert by["ebs-gp2"]["monthly_usd"] == 10.0 and by["ebs-gp2"]["resources"] == ["vol-pvc"], by["ebs-gp2"]
    assert by["eip-unassociated"]["monthly_usd"] == 3.72 and by["eip-unassociated"]["resource"] == "eipalloc-idle"
    assert by["snapshot-old"]["resources"] == ["snap-old"] and by["snapshot-old"]["basis"].startswith("upper bound")
    assert by["nat-no-s3-endpoint"]["monthly_usd"] is None, "an interface endpoint is not a gateway endpoint"
    assert by["lb-no-targets"]["basis"] == "list price" and by["log-no-retention"]["resources"] == ["/forever"]
    assert by["log-no-retention"]["largest"] == [{"name": "/forever", "gib": 1.0}] and HOURS == 744, HOURS
    assert by["eks-extended-support"]["monthly_usd"] == 372.0 and by["rds-extended-support"]["monthly_usd"] == 100.0
    assert facts["tags"]["environment"]["untagged_pct"] == 33.3 and facts["tags"]["project"]["key"] is None, facts["tags"]
    assert by["tag-case-variants"]["resource"] == "Environment, environment", "spellings on resources and in billing are merged"
    assert by["tag-case-variants"]["on_resources"] == {"Environment": 1, "environment": 1}, by["tag-case-variants"]
    assert by["tag-not-activated"]["resource"] == "Project" and by["tag-not-used"]["resource"] == "owner", sorted(by)
    missing = " | ".join(f["detail"] for f in facts["findings"] if f["check"] == "tag-missing")
    assert "Karpenter" in missing and "EBS CSI" in missing, missing
    assert "nat-idle" not in by, "1000 GB through one gateway is not idle"
    idle = nat_idle([{"service": "EC2 - Other", "usage_type": "NatGateway-Hours", "usd": 33.48, "quantity": 744},
                     {"service": "EC2 - Other", "usage_type": "USW2-NatGateway-Bytes", "usd": 0, "quantity": 0.04}], ["nat-1 (vpc-1, us-west-2)"])
    assert idle[0]["monthly_usd"] == 33.48 and idle[0]["basis"] == "bill" and idle[0]["resources"] == ["nat-1 (vpc-1, us-west-2)"], idle
    assert facts["identified_usd"] == round(sum(f["monthly_usd"] or 0 for f in by.values() if not (f["basis"] or "").startswith("upper")), 2)
    subtotal = {r["check"]: r for r in facts["by_check"]}
    assert subtotal["eks-extended-support"] == {"check": "eks-extended-support", "findings": 1, "monthly_usd": 372.0, "kind": "saving"}, subtotal
    assert subtotal["snapshot-old"]["kind"] == "ceiling" and subtotal["nat-no-s3-endpoint"]["kind"] == "not priced", subtotal
    usd = [f["monthly_usd"] for f in facts["findings"]]
    assert usd == sorted([u for u in usd if u is not None], reverse=True) + [None] * usd.count(None), "priced first, highest first"
    assert by["lb-no-targets"]["created_by"] == DIRECT and by["ebs-gp2"]["volumes_by_creator"] == {"EBS CSI driver": 1}, by["ebs-gp2"]
    assert by["tag-not-used"]["region"] is None and facts["tags"]["owner"]["untagged_by_service"] == {}
    assert facts["bill_top"][0]["rate"] == 0.9095 and facts["identified_pct"] == 56.9, (facts["bill_top"][0], facts["identified_pct"])
    assert scan_region("us-east-1", [], ["environment"], date(2026, 11, 12))[3] == ["nat-1 (vpc-1, us-east-1)"], "gateways returned for nat-idle"
    assert subtotal["tag-missing"]["monthly_usd"] is None and subtotal["tag-missing"]["findings"] > 1, subtotal["tag-missing"]
    assert facts["bill_top"][0]["pct"] == 72.8, facts["bill_top"][0]
    assert "111122223333" not in summary(facts) and "vol-" not in summary(facts)
    try:
        aws("ec2", "delete-volume")
        raise AssertionError("a write call was allowed")
    except ValueError:
        pass
    # policy.json must grant exactly what the collector calls (sts:GetCallerIdentity needs no permission)
    iam = {"elbv2": "elasticloadbalancing", "elb": "elasticloadbalancing"}
    used = {f"{iam.get(s, s)}:{op.replace('-', '')}" for s, op in CALLS if s != "sts"}
    policy = json.loads(Path(__file__).with_name("policy.json").read_text())
    granted = {a.lower() for st in policy["Statement"] for a in st["Action"]}
    assert used == granted, f"policy.json drift: missing {sorted(used - granted)}, unused {sorted(granted - used)}"
    print("selftest ok")


def main():
    global PROFILE
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile")
    ap.add_argument("--tags", default="environment,project,owner", help="required tag keys, matched case-insensitively")
    ap.add_argument("--regions", help="comma separated; default is every region with spend last month")
    ap.add_argument("--out", default="facts.json")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    PROFILE = a.profile
    try:
        facts = collect(a.tags.split(","), a.regions.split(",") if a.regions else None)
    except AwsError as e:
        sys.exit(f"cannot identify the caller, check the profile and login: {e}")
    Path(a.out).write_text(json.dumps(facts, indent=1) + "\n")
    print(summary(facts))
    print(f"wrote {a.out} (holds account ids and resource names, keep it out of git)")


if __name__ == "__main__":
    main()
