# AWS cost audit - account 111122223333

> Example output of the aws-cost-audit skill, written from [facts.json](facts.json). The account is synthetic: it was built from fixtures and no real AWS account is behind it.

- Account: 111122223333
- Billing period: 2026-10-01 to 2026-11-01 (end exclusive)
- Regions scanned: us-east-1, us-west-2
- Required tags: environment, project, owner (matched case-insensitively)
- Collected: 2026-11-13

This audit only read from the account; nothing was changed. All figures are monthly and amortized, without credits, refunds and tax. Every number comes from `facts.json`, next to this file. Both files hold the account id and resource names: keep them out of git.

## Summary

- **Most urgent: $372.00 per month starts on 2026-12-01.** The `prod` EKS cluster runs Kubernetes 1.34, which leaves standard support that day. Upgrade before then and the charge never starts.
- **Spend last month: $10,200.61.** $9,972.39 in us-east-1, $23.72 in us-west-2, $204.50 global.
- **Tagging.** Nothing can be attributed to `owner`: no spend in Cost Explorer carries that tag. For `Environment` and `Project`, $7,205.87 of the bill (70.6%) has no value.
- **Identified savings: $553.36 per month, 5.4% of the bill.** Idle and mispriced resources, each named below with its fix.
- **Ceiling, kept separate: $322.00 per month (3.2%).** Log and snapshot storage priced at its maximum. The real saving is lower.

## Coming up

### eks-extended-soon: `prod`, starts 2026-12-01

`prod` (us-east-1) runs Kubernetes 1.34, which leaves standard support on 2026-12-01. From that date extended support adds $372.00 per month unless the cluster is upgraded. It is the same charge `staging` already pays. Fix: upgrade `prod` by one minor version before 2026-12-01. Take `staging` up first: it has to pass 1.34 anyway, and its last step is the rehearsal for prod.

```bash
aws eks describe-cluster-versions --region us-east-1 --output table   # versions in standard support
aws eks update-cluster-version --region us-east-1 --name prod --kubernetes-version <next minor version>
# backstop, can be set today because prod is still in standard support:
aws eks update-cluster-config --region us-east-1 --name prod --upgrade-policy supportType=STANDARD
```

With the backstop AWS upgrades the control plane itself on that date instead of charging: no fee, but a forced upgrade. Set it only if that is acceptable for prod. If the cluster is in Terraform, make both changes there, same block as for `staging`.

## Savings

| Check | What | Where | Monthly | Basis |
|---|---|---|---|---|
| eks-extended-support | EKS cluster `staging` on Kubernetes 1.32, past standard support | us-east-1 | $372.00 | list price: $0.60 per hour instead of $0.10 |
| ebs-unattached | 4 EBS volumes attached to nothing | us-east-1, us-west-2 | $85.00 | bill rate |
| ebs-gp2 | 14 volumes, 2400 GiB, still on gp2 | us-east-1 | $48.00 | bill rate, gp3 costs 20% less per GiB |
| lb-no-targets | 2 load balancers with no registered targets | us-east-1 | $33.48 | bill rate |
| eip-unassociated | 4 Elastic IPs associated with nothing | us-east-1, us-west-2 | $14.88 | bill rate |

"Bill rate" is this account's own average rate for that usage type last month. "List price" is the public us-east-1 price.

### eks-extended-support: $372.00

`staging` runs Kubernetes 1.32, which left standard support on 2026-03-23. Since then its control plane is billed at $0.60 per hour instead of $0.10. On the bill this is the `AmazonEKS-Hours:extendedSupport` line, $446.40 last month. The saving is less than that line because the cluster still pays the standard rate after the upgrade.

Fix: upgrade the control plane one minor version at a time until it runs a version in standard support, then the node groups and add-ons. Do not stop at 1.34: it leaves standard support on 2026-12-01. `aws eks describe-cluster-versions` lists the versions in standard support. With `STANDARD` set afterwards, AWS upgrades the control plane itself when standard support ends: no extended fee again, but a forced upgrade if the date is missed.

```hcl
resource "aws_eks_cluster" "staging" {
  name    = "staging"
  version = "<next minor version>" # apply, let it finish, repeat until past 1.34
  # add after the last upgrade, it is rejected while the cluster is in extended support:
  upgrade_policy { support_type = "STANDARD" }
}
```

### ebs-unattached: $85.00

| Volume | Region | Size | Created | Created by | Monthly |
|---|---|---|---|---|---|
| vol-00000000000000028 | us-east-1 | 500 GiB gp3 | 2025-03-11 | no controller (Terraform, console or CLI) | $40.00 |
| vol-00000000000000384 | us-west-2 | 200 GiB gp2 | 2024-11-05 | no controller (Terraform, console or CLI) | $20.00 |
| vol-00000000000000027 | us-east-1 | 150 GiB gp2 | 2025-08-19 | EBS CSI driver | $15.00 |
| vol-00000000000000026 | us-east-1 | 100 GiB gp2 | 2025-06-02 | EBS CSI driver | $10.00 |

Check first: the CSI volumes back, or used to back, a PersistentVolume. Look for the volume id in both clusters. `Bound` means a workload scaled to zero still claims it: leave it. For the others, snapshot before deleting if the data might matter, and if the volume is defined in Terraform remove it there instead.

```bash
# CSI volumes, in prod and in staging: is a PersistentVolume still pointing at them?
kubectl get pv -o custom-columns=PV:.metadata.name,VOL:.spec.csi.volumeHandle,PHASE:.status.phase \
  | grep -e vol-00000000000000026 -e vol-00000000000000027

# then per volume (use --region us-west-2 for vol-00000000000000384)
aws ec2 create-snapshot --region us-east-1 --volume-id vol-00000000000000028 --description "before delete"
aws ec2 delete-volume   --region us-east-1 --volume-id vol-00000000000000028
```

### ebs-gp2: $48.00

14 volumes, 2400 GiB, all in us-east-1 and all created by the EBS CSI driver: vol-00000000000000017, vol-00000000000000018, vol-00000000000000019, vol-0000000000000001a, vol-0000000000000001b, and the rest in `facts.json`. None is over 1000 GiB, so gp3 needs no extra IOPS to match. For the existing volumes gp3 is an online change with no downtime: run `aws ec2 modify-volume --region us-east-1 --volume-id <volume id> --volume-type gp3` per volume.

New volumes: every one came from the CSI driver, so the source is the StorageClass. The parameters of an existing class cannot be edited: add a gp3 class and make it the default.

```yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: gp3
  annotations:
    storageclass.kubernetes.io/is-default-class: "true"
provisioner: ebs.csi.aws.com
parameters:
  type: gp3
volumeBindingMode: WaitForFirstConsumer
allowVolumeExpansion: true
```

Remove the default annotation from the old class and repoint anything that names the old class in `storageClassName`. `facts.json` does not say which cluster the volumes belong to: check `kubectl get storageclass` in both.

### lb-no-targets: $33.48

- `k8s-staging-grafana-3f9a1c2d4e`: network load balancer created by the AWS Load Balancer Controller, $16.74. Do not delete it in AWS, the controller owns it. Delete or change the Service behind it and the controller removes the load balancer.
- `legacy-api`: application load balancer not created by a controller, $16.74. Delete it in AWS, or in Terraform if it is defined there.

Check first: for `legacy-api`, that no DNS record points at it and that it is not a standby or an environment scaled down on purpose. For Grafana, that nobody in staging still expects that hostname to answer.

```bash
kubectl get svc -A -o wide | grep k8s-staging-grafana-3f9a1c2d4e   # finds the owning Service
kubectl -n <namespace> delete svc <service>                         # or set it to ClusterIP in the chart values

aws elbv2 delete-load-balancer --region us-east-1 --load-balancer-arn <arn of legacy-api>
```

### eip-unassociated: $14.88

eipalloc-00000000000000001, eipalloc-00000000000000002 and eipalloc-00000000000000003 in us-east-1, eipalloc-00000000000000384 in us-west-2. $3.72 each.

Check first: a released address does not come back, so confirm that no partner or firewall allowlists it.

```bash
for id in eipalloc-00000000000000001 eipalloc-00000000000000002 eipalloc-00000000000000003; do
  aws ec2 release-address --region us-east-1 --allocation-id "$id"
done
aws ec2 release-address --region us-west-2 --allocation-id eipalloc-00000000000000384
```

us-west-2: everything found there is a leftover (the volume and the address above, plus 2 empty log groups under Ceilings). The region cost $23.72 last month and stops costing once the volume and the address are gone.

## Ceilings

Upper bounds, not savings. They are not part of the $553.36.

| Check | What | Where | Monthly ceiling | Basis |
|---|---|---|---|---|
| log-no-retention | Log groups that never expire | 48 in us-east-1 (5900 GiB stored), 2 in us-west-2 (0.0 GiB stored) | $177.00 | upper bound: current storage at bill rate; the saving depends on the retention chosen |
| snapshot-old | 60 snapshots older than 90 days, 2900 GiB of source volumes | us-east-1 | $145.00 | upper bound: full volume size at bill rate, snapshots are incremental |

### log-no-retention: $177.00 at most

Why a ceiling: $177.00 is what everything stored in these groups costs today. A retention period only deletes what is older than it, so the saving is the part of the data that is past the retention you choose.

- us-east-1, 48 groups, $177.00. The largest: `/aws/containerinsights/prod/application` (2900 GiB), `/aws/eks/prod/cluster` (1150 GiB), `/aws/containerinsights/prod/dataplane` (700 GiB), `/aws/vpc/flowlogs/prod` (640 GiB), `/aws/rds/instance/app-db/postgresql` (200 GiB). The rest are Lambda log groups, listed in `facts.json`.
- us-west-2, 2 groups, $0.00: `/aws/lambda/poc-thumbnailer`, `/aws/lambda/poc-webhook`.

Fix: set `retention_in_days`, starting with the largest groups above. Groups created by Lambda, EKS or RDS on first write are not in Terraform: declare and import them. Retention deletes everything older than it, so export what has to be kept before applying. Only the storage line shrinks; the ingestion lines (see Where the money goes) do not.

```hcl
import {
  to = aws_cloudwatch_log_group.containerinsights_application
  id = "/aws/containerinsights/prod/application"
}
resource "aws_cloudwatch_log_group" "containerinsights_application" {
  name              = "/aws/containerinsights/prod/application"
  retention_in_days = <days>
}
```

### snapshot-old: $145.00 at most

Why a ceiling: each snapshot is priced as if it held its full source volume. Snapshots are incremental and store only changed blocks, so they cost less than that. For scale, the whole snapshot line on the bill is $170.00.

60 snapshots in us-east-1 are older than 90 days, the oldest from 2023-01-14. 12 more back an AMI and were left out; deregister the AMI first if it is unused. `facts.json` lists 50 of the ids, `aws ec2 describe-snapshots --owner-ids self` gives all of them.

Fix: delete what is not needed, then add an `aws_dlm_lifecycle_policy` with a retention rule so the pile does not grow back. DLM only expires the snapshots it creates, so retire whatever creates them today.

Check first: that a snapshot is not the only copy of a volume that no longer exists and that someone still needs.

```bash
aws ec2 delete-snapshot --region us-east-1 --snapshot-id <snapshot id>
```

## Worth checking

### nat-no-s3-endpoint: vpc-0a1b2c3d4e5f60001

The VPC (us-east-1) has 3 NAT gateways and no S3 gateway endpoint, so S3 traffic and ECR image layers, which are served from S3, pay NAT data processing. NAT processing for the whole account was $414.00 for 9200 GB last month. The S3 share of that is not estimated: it needs VPC flow logs.

Fix: add the endpoint to the private route tables. It is free and takes most image pull traffic off the NAT gateways. Before applying, look for bucket policies that match on the NAT gateways' addresses: they need `aws:SourceVpce` instead, or requests are denied once the routes change.

```hcl
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = "vpc-0a1b2c3d4e5f60001"
  service_name      = "com.amazonaws.us-east-1.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [<private route table ids>]
}
```

## Tagging

| Required tag | Key in billing | Usable for cost allocation | Spend with no value | Share |
|---|---|---|---|---|
| environment | `Environment` | yes | $7,205.87 | 70.6% |
| project | `Project` | yes | $7,205.87 | 70.6% |
| owner | none | no | n/a | n/a |

The amounts count only the spelling shown. Spend tagged `environment` or `project` in lowercase counts as untagged, and both spellings are in use (see case variants), so part of that figure can be spend tagged under the other spelling. `facts.json` does not say how much. The split by service is identical for `Environment` and `Project`:

| Service | Untagged | Resource tags scanned |
|---|---|---|
| Amazon Elastic Compute Cloud - Compute | $4,502.40 | yes, instances below |
| EC2 - Other | $1,233.32 | EBS volumes and NAT gateways only, volumes below |
| AmazonCloudWatch | $1,139.00 | no |
| Amazon Simple Storage Service | $123.60 | no |
| Amazon Elastic Load Balancing | $111.13 | yes, load balancers below |
| Amazon Virtual Private Cloud | $40.92 | no |
| Amazon EC2 Container Registry (ECR) | $38.00 | no |
| Amazon Route 53 | $17.50 | no |

For the services marked "no" the scan does not read tags on log groups, buckets, public addresses, repositories or hosted zones, so there is no resource list. Tag them where they are defined. Log groups created on first write get their tags through the same import as the retention fix.

### Resources missing tags (tag-missing)

Every resource below lacks all of environment, project and owner. The pattern: RDS, ElastiCache and EKS have no untagged spend, while what the controllers inside the clusters create is untagged. Terraform `default_tags` only reaches resources Terraform creates, so each controller needs its own setting.

| Created by | Resources | Set the tags in | What applying it does |
|---|---|---|---|
| Karpenter | 20 EC2 instances, 20 EBS volumes (us-east-1) | `spec.tags` on the `EC2NodeClass`. Covers instances, volumes and network interfaces | Karpenter treats it as drift and replaces the nodes within their disruption budgets |
| EBS CSI driver | 16 EBS volumes (us-east-1) | `controller.extraVolumeTags` in the Helm values or the EKS add-on configuration | New volumes only. Existing ones need `aws ec2 create-tags` once |
| AWS Load Balancer Controller | 6 load balancers (us-east-1) | `defaultTags` in the Helm values | Applied to the existing load balancers on the next reconcile |
| EKS managed node group | 2 EC2 instances (us-east-1) | Launch template `tag_specifications` for `instance`, `volume` and `network-interface`. Tags on the node group itself do not reach the instances | The new template version rolls the nodes |
| the instance it is attached to | 2 EBS volumes (us-east-1) | The `volume` entry in that instance's launch template `tag_specifications`, or `volume_tags` on an `aws_instance` | Follows the instance |
| no controller (Terraform, console or CLI) | 1 EBS volume (us-west-2) | Nowhere: it is vol-00000000000000384, the unattached volume to delete | Nothing |

- Karpenter first: its instances are most of the untagged compute line. Applying replaces nodes, so check the disruption budgets on the prod NodePools before merging.
- The 16 CSI volumes include vol-00000000000000026 and vol-00000000000000027, the unattached ones. Delete those instead of tagging them.
- The load balancers are `k8s-prod-api-5c1d9e7a3b`, `k8s-prod-web-91be04f2aa`, `k8s-prod-admin-2f7c6d10e4`, `k8s-prod-grpc-77a0c3b9d1`, `k8s-prod-kafkaui-0d4e8a6f52` and the Grafana one under Savings.
- `facts.json` does not say which instances the 2 volumes "created by the instance" hang off. If they are the node group instances, the launch template change covers them. All instance and volume ids are in `facts.json`.

```yaml
apiVersion: karpenter.k8s.aws/v1
kind: EC2NodeClass
metadata:
  name: <nodeclass name>
spec:
  tags:
    Environment: <environment>
    Project: <project>
    Owner: <owner>
```

### Case variants (tag-case-variants)

`Environment` and `environment`, `Project` and `project` are all in use. Tag keys are case-sensitive, so each spelling is a separate tag and the spend splits between them. Keep `Environment` and `Project`, the spellings billing already uses, and `Owner` as it is already spelled on resources. Retag the rest (`aws resourcegroupstaggingapi get-resources --region us-east-1 --tag-filters Key=environment` finds them), then enforce the spelling with an Organizations tag policy.

### Not activated (tag-not-activated)

`Owner` is on 13 scanned resources, but Cost Explorer has no spend under it: the key is not activated as a cost allocation tag in the management (payer) account. Until it is, no amount of tagging puts an owner on the bill.

```hcl
resource "aws_ce_cost_allocation_tag" "owner" {
  tag_key = "Owner"
  status  = "Active"
}
```

The tag shows in Cost Explorer about a day later and only for spend from then on, unless a backfill is requested. There is no `tag-not-used` finding: every required key is on at least some resources.

## Where the money goes

The bill lines in `facts.json` (`bill_top`) cover 100.0% of the bill. These are the 15 largest plus load balancer hours, which a finding is priced against, grouped by theme. A line no check flagged gets no savings figure.

| Theme | Line | Amount | Share | Quantity, average rate | What it is, what to look at |
|---|---|---|---|---|---|
| Compute | EC2 `BoxUsage:m6i.2xlarge` | $3,428.35 | 33.6% | 8928 Hrs at $0.384 | Instance hours. See the note under the table |
| Compute | EC2 `BoxUsage:c6i.xlarge` | $1,011.84 | 9.9% | 5952 Hrs at $0.17 | Instance hours. See the note under the table |
| Compute | RDS `Multi-AZUsage:db.r6g.xlarge` | $669.60 | 6.6% | 744 Hrs at $0.90 | Multi-AZ database instance, already on Graviton. Size not assessed |
| Compute | ElastiCache `NodeUsage:cache.r6g.large` | $306.53 | 3.0% | 1488 Hrs at $0.206 | Cache nodes, already on Graviton. Size not assessed |
| Logs | CloudWatch `DataProcessing-Bytes` | $740.00 | 7.3% | 1480 GB at $0.50 | Application logs ingested. Retention does not touch it. Find the groups with the most incoming bytes (`/aws/containerinsights/prod/application` first), debug logging left on, and rarely queried logs that fit the Infrequent Access class |
| Logs | CloudWatch `VendedLog-Bytes` | $210.00 | 2.1% | 420 GB at $0.50 | Logs AWS services deliver: here `/aws/vpc/flowlogs/prod` and `/aws/eks/prod/cluster`. Flow logs can go to S3 for a fraction of the price. Switch off EKS control plane log types nobody reads |
| Logs | CloudWatch `TimedStorage-ByteHrs` | $189.00 | 1.9% | 6300 GB-Mo at $0.03 | Log storage. The log-no-retention ceiling is against this line |
| Network | EC2 - Other `DataTransfer-Regional-Bytes` | $610.00 | 6.0% | 61000 GB at $0.01 | Traffic between Availability Zones, billed in both directions. Pods talking to the database and cache in another zone, topology aware routing or `trafficDistribution: PreferClose` on busy Services, each private subnet routing to the NAT gateway in its own zone |
| Network | EC2 - Other `NatGateway-Bytes` | $414.00 | 4.1% | 9200 GB at $0.045 | Everything private subnets send through NAT. The S3 endpoint under Worth checking first. Then a CloudWatch Logs interface endpoint (without one the 1480 GB of application logs leave through NAT), image pulls from outside ECR, metrics shipped to a vendor |
| Network | ELB `LoadBalancerUsage` | $150.66 | 1.5% | 6696 Hrs at $0.0225 | Load balancer hours. lb-no-targets under Savings |
| Network | CloudFront `US-DataTransfer-Out-Bytes` | $187.00 | 1.8% | 2200 GB at $0.085 | The CDN doing its job. Nothing to do |
| Storage | S3 `TimedStorage-ByteHrs` | $483.00 | 4.7% | 21000 GB-Mo at $0.023 | Object storage. Storage classes and lifecycle are not checked. The rate is the S3 Standard price, so little or nothing sits in a cheaper class: look at lifecycle rules on the largest buckets |
| Storage | EC2 - Other `EBS:VolumeUsage.gp2` | $265.00 | 2.6% | 2650 GB-Mo at $0.10 | ebs-gp2 and ebs-unattached under Savings |
| Storage | EC2 - Other `EBS:VolumeUsage.gp3` | $210.40 | 2.1% | 2630 GB-Mo at $0.08 | Includes the unattached vol-00000000000000028 under Savings |
| Storage | EC2 - Other `EBS:SnapshotUsage` | $170.00 | 1.7% | 3400 GB-Mo at $0.05 | snapshot-old under Ceilings |
| EKS | EKS `AmazonEKS-Hours:extendedSupport` | $446.40 | 4.4% | 744 Hrs at $0.60 | `staging` at the extended rate. eks-extended-support under Savings |

Compute note: the EC2 instance lines are the largest item on the bill and no check covers them. Their average rates are the on-demand prices for these types, so no Savings Plan, reservation or Spot discount reaches them. Commitment coverage and rightsizing are not assessed by this audit; at this share of the bill that is the next piece of work. Also look at Graviton equivalents of m6i and c6i and at whether staging nodes need to run nights and weekends.

The other lines at 1.0% or more (S3 data transfer out, NAT gateway hours, RDS storage) and everything smaller are in `facts.json`.

## Not checked

No call failed, so every check ran in both regions.

Scope limits, so a clean result is not read as a full audit:

- One account per run. Other accounts need their own run, or a run from the payer account for the bill part only.
- Only regions with spend last month were scanned: us-east-1 and us-west-2.
- No check exists yet for Savings Plans and reservation coverage, rightsizing, S3 storage classes and lifecycle, data transfer, idle RDS or stopped instances. The report explains those bill lines and puts no savings figure on them.
- Resource-level tags are checked for EC2 instances, EBS volumes, NAT gateways, load balancers, EKS clusters and RDS instances only.
- A load balancer is flagged when it has no registered targets. One with targets and no traffic is not detected.
- List prices are us-east-1 prices.
