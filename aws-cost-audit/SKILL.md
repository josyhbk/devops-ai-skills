---
name: aws-cost-audit
description: Read-only AWS cost and tagging audit. Collects last month's bill and the idle, mispriced and untagged resources behind it with a stdlib script, then writes a savings report where every dollar figure traces back to the bill or is labelled as a list price or an upper bound. Use when asked to audit AWS cost, find savings or waste, check tagging or cost allocation coverage, or explain an AWS bill.
---

# AWS cost audit

The script computes, you explain. `collect.py` reads the bill and the resources and writes `facts.json` with every number already worked out. Your job is the report: what matters, in plain language, and the fix.

Two rules hold everywhere:

- **No number that is not in `facts.json`.** Never add, subtract, multiply or divide figures yourself. Totals, subtotals per check, shares of the bill and unit rates are all in there. If the point you want to make needs a number that is missing, make it in words or leave it out.
- **Formatting is not arithmetic.** Write money with two decimals and thousands separators (`10080.53` becomes `$10,080.53`, `365.0` becomes `$365.00`) and percentages as given. Rates and quantities go in as they are, without a trailing `.0` and in plain decimals (`5e-06` becomes `0.000005`).

`$SKILL` is the directory this file lives in.

## Workflow

1. **Confirm the account.** Ask which profile to use, run `aws sts get-caller-identity --profile P` and tell the user the account before going on. The audit only reads, but Cost Explorer bills $0.01 per request and a run makes about 5.
2. **Collect.** Ask which tag keys the team requires; the three below are only the default and matching is case-insensitive. Run it from a directory outside any git repo:

   ```bash
   python3 $SKILL/collect.py --profile P --tags environment,project,owner --out facts.json
   ```

   `--regions a,b` overrides the default of every region with spend last month.
3. **Read `facts.json`**, including `errors`. A failed call means that check did not run for that region. It does not mean the check passed.
4. **Write `report.md`** next to `facts.json`, following the outline below. Both files hold the account id and resource names: keep them out of git.
5. **Change nothing in the account.** The fixes go in the report for the user to apply.

If the caller lacks permissions, `policy.json` is the exact read-only policy the collector needs.

## What is in facts.json

| Field | Meaning |
|---|---|
| `account`, `collected`, `period` | Who, when the scan ran, and the billing month (`end` is exclusive) |
| `regions_scanned`, `required_tags` | What the run covered. Both go in the report header |
| `total_usd`, `spend_by_region` | Last full month from Cost Explorer, without credits, refunds and tax. Amortized cost: Savings Plan and reservation fees are spread over the usage they cover |
| `bill_top` | The largest bill lines, 40 at most: `usd`, `pct` of the bill, `quantity` with its `unit`, and `rate` (usd per unit). `bill_top_pct` is the share of the bill these lines cover |
| `tags` | Per required tag: `key` as billing spells it, and the spend with no value for it (`untagged_usd`, `untagged_pct` of `total_usd`, and `untagged_by_service` for the 8 largest services). `key: null` means no spend in Cost Explorer carries the tag, so nothing can be attributed to it. The figures are for the spelling in `key`: spend tagged under another spelling counts as untagged |
| `findings` | One per problem, priced ones first, highest first. See the fields below |
| `by_check` | Per check: number of findings, monthly subtotal, and `kind`: `saving`, `ceiling` or `not priced`. A check that is not listed found nothing, provided `errors` has no failed call for it |
| `identified_usd`, `identified_pct` | Sum of the `saving` findings, and its share of the bill |
| `upper_bound_usd`, `upper_bound_pct` | Sum of the `ceiling` findings, kept apart on purpose |
| `upcoming_usd` | Monthly cost that has not started yet and can still be avoided (see `starts` on the findings) |
| `errors` | Calls that failed |

Fields on a finding:

- `check` and `detail`: the check that fired and one line on what it found. Sizes, dates and versions in `detail` can be quoted as written. An amount in `detail` is account-wide, so it can differ by cents from a single `bill_top` line.
- `region`: null means account-wide.
- `resource`: the id, or a count such as "48 log groups". `resources` lists the ids behind a count, 50 at most; when the count is higher, the `evidence` command returns all of them. On `log-no-retention` the list is sorted largest first and `largest` gives the size of the top five.
- `monthly_usd` and `basis`: `bill` is the amount on the bill. `bill rate` is this account's own average rate for that usage type, applied to the resource. `list price` is the us-east-1 public price: used when the bill has no matching line, and for EKS extended support, where the saving is the difference between two published rates. `upper bound` is a ceiling. Hourly resources are priced for every hour of the billing month and storage is priced at its size on the day of the scan, so a finding can come out slightly above its line on the bill when a resource did not exist all month or has grown since. Say so in one sentence if a reader could notice. When `monthly_usd` is null, `basis` says why there is no figure: it could not be estimated, or the cost has not started yet. Tag findings have neither.
- `evidence`: the command that reproduces the finding, where one command can.
- `created_by` (on `tag-missing`, `ebs-unattached`, `lb-no-targets`) and `volumes_by_creator` (on `ebs-gp2`): what created the resource. It decides where the fix goes.
- `missing` (on `tag-missing`): the required tags the resources lack.
- `on_resources` (on `tag-case-variants`): how many scanned resources carry each spelling.
- `starts`, `then_usd` (on `eks-extended-soon`): the date a cost begins and how much it will be per month.

## Report outline

Write the sections in this order. Aim for something an engineering lead reads in ten minutes: for a small account that is 250 to 300 lines.

1. **Header.** Account, billing period, regions scanned, required tags, date collected. One line saying the audit only read from the account and that figures are monthly and amortized.
2. **Summary.** Four or five bullets:
   - if `upcoming_usd` is not zero, first: the amount and the earliest `starts` date. This is the most urgent line in the report, so label it that way;
   - last month's spend;
   - tagging: if any required tag has `key: null`, say first that nothing can be attributed to it. Then the highest `untagged_pct` among the others;
   - `identified_usd` per month and `identified_pct`, called identified savings;
   - `upper_bound_usd`, separately, called a ceiling.
3. **Coming up.** Only when `upcoming_usd` is not zero: the `eks-extended-soon` findings with `starts` and `then_usd`, and the fix. It goes before the savings because it has a deadline.
4. **Savings.** One table with a row per check whose `kind` is `saving`, highest first: what, where, monthly (the `by_check` subtotal), basis. Then a short subsection per row: the resources and the fix.
5. **Ceilings.** The same for checks whose `kind` is `ceiling`. Say in one sentence why each figure is a ceiling and not a saving.
6. **Worth checking.** The other findings with no `monthly_usd` that are not `tag-*`. Do not guess a number for them.
7. **Tagging.** A table of the required tags: key in billing, usable or not, untagged amount and share. Then `untagged_by_service`. Then the `tag-missing` findings grouped by `created_by`, with where to set the tags. Then `tag-case-variants`, `tag-not-activated` and `tag-not-used`. Services with untagged spend that the scan does not cover (logs, S3, ECR, Route 53 and the like) are named from `untagged_by_service` with no resource list.
8. **Where the money goes.** The `bill_top` lines with `pct` of 1.0 or more: the 15 largest, plus any below them whose row in "Reading the bill" names a check that has findings, so every line a finding is priced against is shown. Grouped by theme, using "Reading the bill" below. For each: amount, `pct`, what it is, what to look at. You may quote `quantity`, `unit` and `rate`. Say what share of the bill all the lines in `bill_top` cover (`bill_top_pct`), including the ones not shown. A line the checks did not flag gets no savings figure.
9. **Not checked.** Every entry in `errors`, or one line saying no call failed. Then the scope limits at the bottom of this file.

Rules that apply to all sections:

- Counts are numbers too. When a check has findings in several regions, list them side by side ("48 in us-east-1, 2 in us-west-2") or name the resources; do not add counts up.
- Name up to five resources inline per check and point to `facts.json` for the rest.
- Findings priced at 0.00 share one line. They do not get a row each.
- A fix is the change, not a tutorial: at most one snippet per check, 15 lines at most. For `tag-missing`, one table for all creators and one snippet for the creator behind the largest single finding. Use Terraform or Kubernetes when the fix is configuration. Use an `aws` command for the user to run when it is a one-off deletion or an in-place change. Use the real names from the finding, and `<placeholders>` for what `facts.json` does not know.
- Every deletion gets one line on what to check first.
- A resource can sit under two findings, such as an unattached volume that is also missing tags. Say so where it matters: nobody should tag what is about to be deleted.
- No "up to X%" claims and no annualised totals unless the user asks.

## Fixes

| Check | Fix |
|---|---|
| `ebs-unattached` | Snapshot it if the data might matter, then delete it. If `created_by` is the EBS CSI driver it backs, or used to back, a PersistentVolume: look for the volume id in `kubectl get pv` first. `Bound` means a workload scaled to zero still claims it |
| `ebs-gp2` | gp3 is an online change with no downtime. Existing volumes: `aws ec2 modify-volume --volume-type gp3` per volume, or `type = "gp3"` where the volume is in Terraform. Then stop new gp2 volumes at the source named in `volumes_by_creator`: for the EBS CSI driver add a gp3 StorageClass and make it the default (the parameters of an existing class cannot be edited); for Karpenter set `volumeType: gp3` in the `EC2NodeClass` block device mappings; for node groups and instances set it in the launch template. Volumes over 1000 GiB had more than 3000 IOPS on gp2, so set `iops` to match |
| `eip-unassociated` | Release the address. A released address does not come back, so check that nobody allowlists it |
| `snapshot-old` | Delete what is not needed and add an `aws_dlm_lifecycle_policy` with a retention rule so the pile does not grow back. Snapshots that back an AMI are already left out of the count; deregister the AMI first if it is unused |
| `nat-no-s3-endpoint` | Add an S3 gateway endpoint to the private route tables (snippet below). It is free, and ECR image layers are served from S3, so it also takes most image pull traffic off the NAT gateway. Bucket policies that match on the NAT gateway's address need `aws:SourceVpce` instead |
| `nat-idle` | Find what the private subnets still need the gateway for. If nothing, delete it and release its Elastic IP |
| `lb-no-targets` | If `created_by` is a Kubernetes controller, do not delete it in AWS: delete or change the Ingress or Service that owns it. Otherwise confirm it is not a standby or an environment scaled down on purpose and that no DNS record points at it, then delete it |
| `log-no-retention` | Set `retention_in_days` on the log group. Groups created by Lambda, EKS or RDS on first write are not in Terraform: declare and import them. Retention deletes everything older than it, so export first what has to be kept. The storage line shrinks; the ingestion line does not |
| `eks-extended-support` | Upgrade the control plane one minor version at a time until it runs a version in standard support (`aws eks describe-cluster-versions` lists them), then the node groups and add-ons. Check the target against the `eks-extended-soon` findings: a version that leaves standard support next month is not far enough. `upgrade_policy { support_type = "STANDARD" }` can only be set while a cluster is in standard support, so it goes in after the upgrade. With it, AWS upgrades the control plane itself when standard support ends: no extended fee, but a forced upgrade if the date is missed |
| `eks-extended-soon` | Upgrade before `starts`. The `upgrade_policy` note above applies |
| `rds-extended-support` | Upgrade the engine major version of the instances named in the finding |
| `tag-not-activated` | If the finding names more than one spelling, settle `tag-case-variants` first and activate the spelling that is kept. Activate the key with `aws_ce_cost_allocation_tag` in the management (payer) account. Tags show in Cost Explorer about a day later and only for spend from then on, unless a backfill is requested |
| `tag-not-used` | Nothing scanned carries the key. Agree the tag standard first, then apply it where tags come from |
| `tag-case-variants` | Keep the spelling billing already uses (`tags.<tag>.key`), or the one most resources carry (`on_resources`) when that is null. Retag the rest and enforce it with an Organizations tag policy |
| `tag-missing` | Set the tags where `created_by` says they come from, see below |

```hcl
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = "<vpc id from the finding>"
  service_name      = "com.amazonaws.<region>.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [<private route table ids>]
}
```

### Where tags come from

Terraform `default_tags` only reaches resources Terraform creates. Anything created by a controller inside the cluster needs its own setting, which is why EKS accounts usually show well-tagged infrastructure and untagged nodes, volumes and load balancers. Say what applying each fix does, because some of them replace nodes.

| `created_by` | Set tags in | What applying it does |
|---|---|---|
| no controller (Terraform, console or CLI) | `default_tags` in the `aws` provider block, or `tags` on the resource | Tags the resource in place |
| Karpenter | `spec.tags` on the `EC2NodeClass`. Covers instances, volumes and network interfaces | Karpenter treats it as drift and replaces the nodes within their disruption budgets |
| EKS managed node group | Launch template `tag_specifications` for `instance`, `volume` and `network-interface`. Tags on the node group itself do not reach the instances | The new template version rolls the nodes |
| Auto Scaling group | The same launch template `tag_specifications` | Running instances keep their tags until an instance refresh |
| the instance it is attached to | `volume_tags` on an `aws_instance`, or the `volume` entry in its launch template `tag_specifications` | Follows the instance's row above |
| EBS CSI driver | `controller.extraVolumeTags` in the Helm values or the EKS add-on configuration | New volumes only. Existing ones need `aws ec2 create-tags` once |
| AWS Load Balancer Controller | `defaultTags` in the Helm values. Per object: the `alb.ingress.kubernetes.io/tags` annotation | Applied to its existing load balancers on the next reconcile |
| Kubernetes service controller | `service.beta.kubernetes.io/aws-load-balancer-additional-resource-tags` annotation on the Service | Check the load balancer after applying |
| CloudFormation | Stack-level tags | Propagated on the next stack update |

Spell the keys the way billing already does (`tags.<tag>.key`), or the way the resources do when that is null (the `tag-not-activated` finding names the spelling), not the way the example below does:

```hcl
provider "aws" {
  default_tags {
    tags = {
      Environment = var.environment
      Project     = var.project
      Owner       = var.owner
    }
  }
}
```

## Reading the bill

On a large account the priced findings are often a small part of the bill, and the big lines are things no resource scan can judge. Explain them with this table. Usage types carry a region prefix in some regions and not in others (`USE1-`, `USE2-`, none), so ignore the prefix when matching. For a line that is not in the table, say what it is only if you are sure; otherwise give the amount and say it was not analysed.

| Usage type contains | What it is | What to look at |
|---|---|---|
| `BoxUsage:`, `InstanceUsage:`, `Multi-AZUsage:`, `NodeUsage:` | Instance hours for EC2, RDS and ElastiCache | Previous generation types (t2, m4, m5), Graviton equivalents, non-production running all night and weekend. Savings Plans and reservations are not assessed here |
| `DataTransfer-Regional-Bytes` | Traffic between Availability Zones, billed in both directions | Clients and the databases, caches or brokers they talk to sitting in different zones. One NAT gateway serving several zones. On EKS, topology aware routing or `trafficDistribution: PreferClose` on busy Services |
| `DataTransfer-Out-Bytes` | Traffic to the internet. The service it is listed under says where it leaves from | Under S3 or a load balancer: downloads served directly, where CloudFront in front is cheaper per GB and the transfer to CloudFront is free. Under CloudFront it is the CDN doing its job |
| `NatGateway-Bytes` | Everything private subnets send through a NAT gateway | The `nat-no-s3-endpoint` findings first. Then image pulls, log and metric shipping to an external vendor, and calls to AWS APIs that an interface endpoint could carry |
| `NatGateway-Hours` | The hourly charge for each NAT gateway | The `nat-idle` finding if there is one. Otherwise nothing |
| `PublicIPv4:InUseAddress` | Every public IPv4 address in use, including the ones on load balancers and NAT gateways | Instances and tasks with a public address that only need outbound access. Internet-facing load balancers that could be internal |
| `PublicIPv4:IdleAddress` | Public addresses attached to nothing | The `eip-unassociated` findings |
| `ClientVPN-EndpointHours`, `ClientVPN-ConnectionHours` | Client VPN: an hourly charge per subnet associated with an endpoint, connected users or not, plus an hourly charge per open connection | Endpoints associated with more subnets than they need, endpoints nobody connects to any more |
| `DataProcessing-Bytes` under CloudWatch | Application logs ingested into CloudWatch | The log groups with the most incoming bytes, debug level logging left on, the Infrequent Access log class for logs that are rarely queried |
| `VendedLog-Bytes` | Logs that AWS services deliver to CloudWatch: VPC flow logs, EKS control plane logs, Route 53 query logs, API Gateway | Flow logs can go to S3 for a fraction of the price. EKS control plane log types nobody reads |
| `DataScanned-Bytes` under CloudWatch | Logs Insights queries, billed per GB scanned | Dashboards and scheduled queries that scan many log groups or long time ranges on every refresh |
| `TimedStorage-ByteHrs` | Storage. Under CloudWatch it is logs, under S3 objects, under ECR images | CloudWatch: the `log-no-retention` findings. S3: storage classes and lifecycle rules, which this audit does not check. ECR: lifecycle policies that expire untagged and old images |
| `EBS:VolumeUsage`, `EBS:SnapshotUsage` | Volume and snapshot storage | The `ebs-unattached`, `ebs-gp2` and `snapshot-old` findings |
| `EBS:VolumeP-IOPS`, `EBS:VolumeP-Throughput` | IOPS and throughput provisioned above the free gp3 baseline | Volumes provisioned high at creation and never measured. Compare against the volume's CloudWatch metrics |
| `LoadBalancerUsage`, `LCUUsage` | Load balancer hours and the traffic they process | The `lb-no-targets` findings. On EKS, Ingresses that each have their own load balancer and could share one through an IngressGroup |
| `AmazonEKS-Hours:perCluster` | The flat fee per EKS cluster | The number of clusters. Small non-production clusters that could share one |
| `AmazonEKS-Hours:extendedSupport` | Clusters on a Kubernetes version past standard support, billed at the extended rate | The `eks-extended-support` findings |
| `Storage-ShardHour` | Kinesis provisioned shards, paid whether data flows or not | Shard count against real throughput. On-demand mode for low or spiky streams |
| `ProtocolHours` | Transfer Family servers, billed per enabled protocol for every hour the server exists | Servers for dev or one partner that could be merged or stopped |
| `UploadBytes`, `DownloadBytes` under Transfer Family | Data moved through those servers, billed per GB on top of the server hours | The rate is fixed, so volume is the only lever: internal jobs that go through SFTP when they could read and write the S3 bucket directly |
| `Fargate-vCPU-Hours`, `Fargate-GB-Hours` | CPU and memory of Fargate tasks and pods, billed on the size requested and not on what is used | Task sizes against real usage. On ECS, Fargate Spot for interruptible work and arm64 task definitions |
| `Lambda-GB-Second`, `Lambda-Provisioned-Concurrency` | Lambda compute: memory size times duration. Provisioned concurrency is billed for every hour it is configured, used or not | Functions with more memory than they need, arm64 where the code allows it, provisioned concurrency left on functions with little traffic |
| `Requests-Tier1` under S3 | PUT, COPY, POST and LIST requests | Applications that write many small objects or list prefixes in a loop |
| A `Savings Plans` or reservation line | Commitment that covered no usage that month. The used part is already spread over the usage lines | Utilization below 100 percent: the commitment is larger than what runs, or the workload moved to something it does not cover |

## Scope limits

Say these in the report so nobody reads a clean result as a full audit:

- One account per run. Run it per account, or from the payer account for the bill part only.
- Only regions with spend last month are scanned.
- No check exists yet for Savings Plans and reservation coverage, rightsizing, S3 storage classes and lifecycle, data transfer, idle RDS or stopped instances. The report explains those bill lines and puts no savings figure on them.
- Resource-level tags are checked for EC2 instances, EBS volumes, NAT gateways, load balancers, EKS clusters and RDS instances only.
- A load balancer is flagged when it has no registered targets. One with targets and no traffic is not detected.
- List prices are us-east-1 prices.
