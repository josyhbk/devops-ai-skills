# devops-ai-skills

Claude Code skills I use for DevOps and platform work.

| Skill | What it does |
|---|---|
| [drawio-aws-diagram](drawio-aws-diagram/) | AWS, EKS and Kubernetes architecture diagrams as draw.io XML, checked by lint and a rendered PNG before they are handed over |

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

## Notes

- AWS icons are the [AWS Architecture Icons](https://aws.amazon.com/architecture/icons/), which AWS provides for architecture diagrams. Kubernetes icons come from draw.io's Kubernetes library. Neither icon set is redistributed here: the skill references draw.io's built-in shape names, and the example PNGs are rendered diagrams.
- `shapes.txt` is generated from [jgraph/drawio](https://github.com/jgraph/drawio) (Apache 2.0) sidebar definitions.

## License

MIT
