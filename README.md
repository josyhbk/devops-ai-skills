# devops-ai-skills

[![ci](https://github.com/josyhbk/devops-ai-skills/actions/workflows/ci.yml/badge.svg)](https://github.com/josyhbk/devops-ai-skills/actions/workflows/ci.yml)

Claude Code skills I use for DevOps and platform work.

| Skill | What it does |
|---|---|
| [drawio-aws-diagram](drawio-aws-diagram/) | AWS, EKS and Kubernetes architecture diagrams as draw.io XML, checked by lint and a rendered PNG before they are handed over |
| [aws-cost-audit](aws-cost-audit/) | Read-only AWS cost and tagging audit where every dollar figure in the report traces back to the bill |

## drawio-aws-diagram

Ask for a diagram, get a `.drawio` file with real AWS and Kubernetes icons. It opens in [diagrams.net](https://app.diagrams.net), imports into Lucidchart, and diffs cleanly in git.

The hard part of generating diagram XML is that the model can't see what it drew. Icon names get guessed and render as empty boxes, arrows run through labels, and shapes look like they're inside a VPC but aren't attached to it. This skill closes the loop:

1. Shape names come from `shapes.txt`, extracted from draw.io's own source, so nothing is guessed.
2. `lint.py` catches the structural problems: unknown shapes, dangling edges, overlapping icons and labels, broken container parenting.
3. The diagram gets rendered to PNG and Claude looks at it, fixes what it sees, and repeats.

3-tier web app:

![three-tier](drawio-aws-diagram/examples/three-tier.drawio.png)

EKS platform with GitOps:

![eks-gitops](drawio-aws-diagram/examples/eks-gitops.drawio.png)

Kubernetes workload:

![k8s-workload](drawio-aws-diagram/examples/k8s-workload.drawio.png)

The `.drawio.png` files are regular images with the diagram embedded. Open one in draw.io and it is fully editable.

### Install

```bash
git clone https://github.com/josyhbk/devops-ai-skills
cp -r devops-ai-skills/drawio-aws-diagram ~/.claude/skills/
```

Or copy it into a repo's `.claude/skills/` to scope it to that project.

Needs `python3` for lint and `docker` for rendering (`rlespinasse/drawio-export`). No pip installs.

### Use

Start a new Claude Code session and ask:

> Draw an EKS cluster in private subnets behind an ALB, with Argo CD and Karpenter, pulling images from ECR

or call it directly with `/drawio-aws-diagram`.

Lint works on its own too:

```bash
python3 drawio-aws-diagram/lint.py my.drawio              # exit 1 on errors
python3 drawio-aws-diagram/lint.py --xml file.drawio.png  # print the embedded XML
```

## aws-cost-audit

Ask for a cost audit, get a report where every dollar figure traces back to the bill.

The hard part of asking a model about AWS cost is that it makes numbers up: "you could save up to 30%". This skill splits the work so it can't:

1. `collect.py` reads last month's bill from Cost Explorer and scans the regions that had spend. It only makes describe, list and get calls and refuses anything else.
2. It writes `facts.json` with every figure already computed and labelled: taken from the bill, priced at the account's own rate, a list price, or an upper bound. What can't be priced honestly gets no number and says why.
3. Claude writes the report from `facts.json`: ranking, explanation, and the fix as Terraform or Kubernetes changes. It does no arithmetic.

What it checks:

- **Tagging.** Which required tags work in Cost Explorer, how much spend carries no value for them, and which resources are missing them. Missing tags are grouped by what created the resource (Terraform, Karpenter, EKS node groups, the EBS CSI driver, the AWS Load Balancer Controller), because that is where the fix goes.
- **Waste.** Unattached EBS volumes, gp2 volumes, idle Elastic IPs, old snapshots, load balancers with no targets, log groups that never expire, NAT gateways with no traffic or with no S3 gateway endpoint next to them.
- **Extended support.** EKS clusters paying for it or about to, and RDS extended support charges.

Example: [report.md](aws-cost-audit/examples/report.md), written from [facts.json](aws-cost-audit/examples/facts.json). The account in the example is synthetic.

### Install

```bash
cp -r devops-ai-skills/aws-cost-audit ~/.claude/skills/
```

Needs `python3` and the AWS CLI v2. No pip installs. `policy.json` is the read-only IAM policy the collector needs. Cost Explorer bills $0.01 per request and a run makes five to ten.

### Use

> Audit the cost of the account behind the staging profile. Required tags are environment, project and owner.

The collector works on its own too:

```bash
python3 aws-cost-audit/collect.py --profile staging --out facts.json
python3 aws-cost-audit/collect.py --selftest
```

One account per run. Savings Plans coverage, rightsizing, S3 lifecycle and data transfer are not checked yet: the report explains those lines of the bill but puts no savings number on them.

## Notes

- AWS icons are the [AWS Architecture Icons](https://aws.amazon.com/architecture/icons/), which AWS provides for architecture diagrams. Kubernetes icons come from draw.io's Kubernetes library. Neither icon set is redistributed here: the skill references draw.io's built-in shape names, and the example PNGs are rendered diagrams.
- `shapes.txt` is generated from [jgraph/drawio](https://github.com/jgraph/drawio) (Apache 2.0) sidebar definitions.

## License

MIT
