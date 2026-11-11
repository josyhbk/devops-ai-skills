---
name: drawio-aws-diagram
description: Author AWS, EKS and Kubernetes architecture diagrams as draw.io (.drawio) XML with the native mxgraph.aws4.* and mxgraph.kubernetes.* shapes, then lint and render them to check the layout before handing them over. Use when asked to create, draw, update or review an architecture, network, infra, EKS or Kubernetes diagram. Output opens in draw.io / diagrams.net and imports into Lucidchart. Deterministic, diffable files, no GUI and no API key.
---

# AWS / EKS / Kubernetes diagrams as draw.io XML

You write the `.drawio` XML directly. You cannot see what you drew, so every diagram goes through lint and a rendered PNG before it is done.

Work in the user's directory, not in the skill's. `$SKILL` below is this skill's directory (usually `~/.claude/skills/drawio-aws-diagram`, or `.claude/skills/drawio-aws-diagram` in a repo). Shell variables don't persist between commands, so write the full path or set `SKILL=...` in the same command.

| File | What it is |
|---|---|
| `starter.drawio` | Clean 3-tier skeleton (Route 53, CloudFront, WAF, ACM, ALB, EC2 x2, RDS). Copy it. |
| `examples/*.drawio.png` | Finished diagrams with the XML embedded: `eks-gitops`, `k8s-workload`, `three-tier`. |
| `shapes.txt` | Every valid AWS and Kubernetes shape name, taken from draw.io's source. |
| `lint.py` | Structural checks. Stdlib Python, no install. |

## Workflow

1. **Start from something that works.** Copy `$SKILL/starter.drawio`, or pull the XML out of an example:
   `python3 $SKILL/lint.py --xml $SKILL/examples/eks-gitops.drawio.png > my.drawio`
2. **Write the XML.** Look up every shape name in `shapes.txt` before using it (`grep -i queue $SKILL/shapes.txt`). Never guess: a wrong name does not error, it renders an empty colored box.
3. **Lint** until it prints `ok`:
   `python3 $SKILL/lint.py my.drawio`
   Catches duplicate ids, edges to missing cells, unknown shape names, icon labels overlapping other icons, and shapes that sit inside a container without being parented to it. It does **not** check edge routes or edge labels, so `ok` says nothing about those.
4. **Render and look.** Render a plain PNG into `preview/` and open `preview/my.png` with your image reader:
   ```bash
   docker run --rm -v "$PWD":/data -e OWNER="$(id -u):$(id -g)" --entrypoint sh rlespinasse/drawio-export:v4.38.0 \
     -c '/opt/drawio-desktop/entrypoint.sh -f png -b 20 --remove-page-suffix -o preview my.drawio >/dev/null && chown -R "$OWNER" preview'
   ```
   Check for arrows crossing icons or labels, edge labels on container borders or touching each other, edges running through a container header, and cramped areas. Fix, then go back to step 3. Usually takes 2 or 3 rounds: expect the first render to have problems even when lint passes.
5. **Deliver.** Hand over the `.drawio`, or export a PNG with the diagram embedded (`-e`) as `my.drawio.png`: it displays as an image on GitHub and opens as an editable diagram in draw.io.
   ```bash
   docker run --rm -v "$PWD":/data -e OWNER="$(id -u):$(id -g)" --entrypoint sh rlespinasse/drawio-export:v4.38.0 \
     -c '/opt/drawio-desktop/entrypoint.sh -f png -e -b 20 --remove-page-suffix -o export my.drawio >/dev/null && chown -R "$OWNER" export' \
     && mv export/my.png my.drawio.png && rmdir export
   ```

The `chown` matters on Linux: Docker writes as root, and without it you can't move or delete the output. Look at the plain PNG in `preview/` yourself, because image readers may reject the embedded variant. Delete `preview/` when you're done.

## File skeleton

```xml
<mxfile host="app.diagrams.net">
  <diagram id="d1" name="My Architecture">
    <mxGraphModel grid="1" gridSize="10" page="1" pageWidth="1400" pageHeight="900">
      <root>
        <mxCell id="0" />
        <mxCell id="1" parent="0" />
        <!-- containers, then icons, then edges -->
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>
```

## Containers and parenting

A shape belongs to a container only when `parent="<container id>"`, and then its `x`/`y` are **relative to the container**. Overlapping coordinates are not enough: it renders, but when someone drags the VPC in draw.io the shapes stay behind. Lint flags this.

Every container style starts with this base:
`outlineConnect=0;gradientColor=none;html=1;whiteSpace=wrap;fontSize=12;fontStyle=0;container=1;pointerEvents=0;collapsible=0;recursiveResize=0;shape=mxgraph.aws4.group;verticalAlign=top;align=left;spacingLeft=30;`
then add the row below. Do not set `labelPosition` or `verticalLabelPosition` on containers: it pushes the label outside the box.

| Container | Add to base style |
|---|---|
| AWS Cloud | `grIcon=mxgraph.aws4.group_aws_cloud_alt;strokeColor=#232F3E;fillColor=none;fontColor=#232F3E;dashed=0;` |
| Region | `grIcon=mxgraph.aws4.group_region;strokeColor=#00A4A6;fillColor=none;fontColor=#147EBA;dashed=1;` |
| AWS Account | `grIcon=mxgraph.aws4.group_account;strokeColor=#CD2264;fillColor=none;fontColor=#CD2264;dashed=0;` |
| VPC | `grIcon=mxgraph.aws4.group_vpc2;strokeColor=#8C4FFF;fillColor=none;fontColor=#AAB7B8;dashed=0;` |
| Public subnet | `grIcon=mxgraph.aws4.group_security_group;grStroke=0;strokeColor=#7AA116;fillColor=#F2F6E8;fontColor=#248814;dashed=0;` |
| Private subnet | `grIcon=mxgraph.aws4.group_security_group;grStroke=0;strokeColor=#00A4A6;fillColor=#E6F6F7;fontColor=#147EBA;dashed=0;` |
| Auto Scaling group | `grIcon=mxgraph.aws4.group_auto_scaling_group;strokeColor=#ED7100;fillColor=none;fontColor=#ED7100;dashed=1;` |

Availability Zone, EKS cluster, node group and Kubernetes namespace have no group icon. Use a plain dashed box:

```xml
<mxCell id="cluster" value="EKS cluster" style="rounded=1;arcSize=2;html=1;whiteSpace=wrap;container=1;collapsible=0;recursiveResize=0;fillColor=none;strokeColor=#ED7100;dashed=1;verticalAlign=top;align=left;spacingLeft=10;fontColor=#ED7100;fontSize=12;" vertex="1" parent="private-subnet">
  <mxGeometry x="20" y="50" width="700" height="510" as="geometry" />
</mxCell>
```

Colors: AZ `#147EBA`, EKS cluster `#ED7100`, node group `#545B64`, namespace `#2875E2`.

Serverless or managed-only designs (Lambda, API Gateway, DynamoDB, no VPC) need just the AWS Cloud container. Add a Region container only when the region matters to the reader, such as multi-region or a us-east-1 requirement.

## AWS service icon (78x78)

```xml
<mxCell id="ec2-a" value="EC2 (AZ a)" style="sketch=0;outlineConnect=0;fontColor=#232F3E;gradientColor=none;fillColor=#ED7100;strokeColor=#ffffff;dashed=0;verticalLabelPosition=bottom;verticalAlign=top;align=center;html=1;fontSize=12;aspect=fixed;shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.ec2;" vertex="1" parent="app-subnet">
  <mxGeometry x="51" y="120" width="78" height="78" as="geometry" />
</mxCell>
```

Change `resIcon=` and `fillColor=` per service. Person icon: `shape=mxgraph.aws4.user;fillColor=#232F3D;strokeColor=none;`, size 52x65, no resIcon.

| Category | fillColor | resIcon names (all in shapes.txt) |
|---|---|---|
| Compute / containers | `#ED7100` | `ec2` `lambda` `ecs` `fargate` `eks` `ecr` |
| Networking | `#8C4FFF` | `route_53` `cloudfront` `application_load_balancer` `network_load_balancer` `nat_gateway` `internet_gateway` `transit_gateway` `endpoints` `api_gateway` |
| Storage | `#7AA116` | `s3` `elastic_block_store` `elastic_file_system` `fsx` |
| Database | `#C925D1` | `rds` `aurora` `dynamodb` `elasticache` |
| Security / identity | `#DD344C` | `identity_and_access_management` `key_management_service` `secrets_manager` `certificate_manager_3` `waf` |
| App integration | `#E7157B` | `sqs` `sns` `eventbridge` `step_functions` |
| Management | `#E7157B` | `cloudwatch_2` `cloudtrail` |
| Analytics | `#8C4FFF` | `athena` `glue` `kinesis_data_firehose` |
| ML | `#01A88D` | `sagemaker` `bedrock` |

For a service not in the table, use the color of the category the AWS console lists it under (Cognito is Security, Identity and Compliance, so `#DD344C`).

Wrong names that look right and render empty: `simple_queue_service` (use `sqs`), `simple_notification_service` (`sns`), `elastic_container_service` (`ecs`).

## Kubernetes icon (50x48)

```xml
<mxCell id="argocd" value="Argo CD" style="aspect=fixed;sketch=0;html=1;dashed=0;whitespace=wrap;verticalLabelPosition=bottom;verticalAlign=top;fillColor=#2875E2;strokeColor=#ffffff;fontSize=12;shape=mxgraph.kubernetes.icon2;prIcon=deploy" vertex="1" parent="system-nodes">
  <mxGeometry x="60" y="50" width="50" height="48" as="geometry" />
</mxCell>
```

Common `prIcon` values: `pod` `deploy` `rs` `sts` `ds` `job` `cronjob` `svc` `ing` `ep` `netpol` `cm` `secret` `pv` `pvc` `sc` `hpa` `ns` `node` `sa` `role` `rb` `crd` `control_plane` `api` `etcd`. Full list at the end of `shapes.txt`.

Third-party controllers (Argo CD, Karpenter, external-secrets) have no icon of their own. Use `prIcon=deploy` with the product name as the label.

## Edges

```xml
<mxCell id="e-cf-alb" value="3. HTTPS :443" style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;endArrow=classic;strokeColor=#545B64;fontColor=#232F3E;fontSize=11;labelBackgroundColor=#ffffff;" edge="1" parent="1" source="cloudfront" target="alb">
  <mxGeometry x="-0.4" relative="1" as="geometry" />
</mxCell>
```

- Put the label in the edge's `value`. `mxGeometry x` slides it along the edge (-1 is the source end, 1 the target end).
- Data flow: `endArrow=classic`, stroke `#545B64`, numbered labels (`1.`, `2.`) for sequences.
- Attachments (WAF or ACM to CloudFront): `endArrow=none;dashed=1;` in the category color, no label.
- Control flow (HPA scales a Deployment): `dashed=1` with an arrow.
- Edges may cross container borders, and may point at a container (`target="workload-nodes"`).

## Layout rules (learned from rendering)

- **Edge leaving an icon from the bottom crosses its label.** Move that icon's label to a free side:
  - left: `labelPosition=left;verticalLabelPosition=middle;align=right;verticalAlign=middle;`
  - right: `labelPosition=right;verticalLabelPosition=middle;align=left;verticalAlign=middle;spacingLeft=6;`
  - top, when edges use both sides and the bottom: `verticalLabelPosition=top;verticalAlign=bottom;`
- **Fan-out edges cut through neighbors.** When one source feeds several targets (ALB to 2 EC2, 2 EC2 to RDS), pin both ends: `exitX=1;exitY=0.5;entryX=0;entryY=0.5;`. Spread entries on a shared target with `entryY=0.25` and `entryY=0.75`.
- **A vertical edge into a container crosses its header text.** Move that container's label to the bottom: `verticalAlign=bottom;spacingBottom=6;`.
- **Edge labels land on container borders.** An L-shaped edge from outside a container often turns right on the border, and its label sits there. Make the turn happen well clear of the border (move the source, or pin `exitX`/`exitY`), or slide the label with `mxGeometry x`.
- **Two L-shaped edges leaving the same side put their labels at neighboring corners**, where they merge into one string. Leave from different sides, or label only one of them.
- **Labeled edges need room.** Leave about 120px between icons joined by a labeled edge. Less than that and a label like "7. enqueue job" touches the icons.
- Snap everything to the 10px grid. Center a 78px icon in a 180px column at `x=51`. Rows about 100px apart read well with labels underneath.
- Order in the file: containers, icons, edges.

## Lucidchart

**File > Import > draw.io** keeps shapes, containers, edges and labels. `mxgraph.aws4.*` renders the AWS 2023 icon set: for an existing Lucid diagram in the 2019/2021 style, edit in Lucid instead of re-importing, or every icon changes.

## When not to use this

For a quick sketch with no AWS icons, a Mermaid block in markdown is faster.
