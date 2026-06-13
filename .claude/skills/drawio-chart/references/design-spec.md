# Draw.io 视觉规范

> 所有配图严格遵循此规范，不准偏离。

## 配色系统

### 核心语义类别

| 语义类别 | 填充色 | 文字色 | 边框色 | 用途 |
|---------|-------|--------|--------|------|
| **Gateway/Entry** | `#005D7B` | `#FFFFFF` | `none` | API网关、负载均衡、入口点 |
| **Business Service** | `#E99151` | `#FFFFFF` | `none` | 核心业务服务、领域服务 |
| **Infrastructure Service** | `#7C3AED` | `#FFFFFF` | `none` | 基础设施服务（认证、日志、监控） |
| **Client/Frontend** | `#0891B2` | `#FFFFFF` | `none` | 前端、用户、客户端 |
| **External/3rd Party** | `#64748B` | `#FFFFFF` | `none` | 外部API、第三方服务 |

### 数据存储类别

| 语义类别 | 填充色 | 文字色 | 边框色 | 形状 | 用途 |
|---------|-------|--------|--------|------|------|
| **Primary DB** | `#E99151` | `#FFFFFF` | `none` | cylinder3 | 主数据库、核心存储 |
| **Replica DB** | `#E4C189` | `#2D3748` | `none` | cylinder3 | 从库、只读副本 |
| **Cache** | `#4CA497` | `#FFFFFF` | `none` | rounded | 缓存服务 |
| **Message Queue** | `#4CA497` | `#FFFFFF` | `none` | rounded | 消息队列 |
| **Search Engine** | `#0891B2` | `#FFFFFF` | `none` | rounded | 搜索引擎 |
| **Object Storage** | `#7C3AED` | `#FFFFFF` | `none` | cylinder3 | 对象存储 |

### 状态类别

| 语义类别 | 填充色 | 文字色 | 边框色 | 用途 |
|---------|-------|--------|--------|------|
| **Success/Status** | `#4CA497` | `#FFFFFF` | `none` | 正常流、成功状态 |
| **Alert/Danger** | `#DC2626` | `#FFFFFF` | `none` | 异常流、错误状态 |
| **Warning/Retry** | `#E99151` | `#FFFFFF` | `none` | 重试、降级、熔断状态 |
| **Info/Neutral** | `#94A3B8` | `#FFFFFF` | `none` | 中性状态、待处理 |

### 容器类别

| 语义类别 | 填充色 | 文字色 | 边框色 | 用途 |
|---------|-------|--------|--------|------|
| **Group/Infra** | `none` | `#2D3748` | `#005D7B` | 容器、网络、分组区域（虚线） |
| **Network Zone** | `#F8FAFC` | `#2D3748` | `#E2E8F0` | 网络分区、安全域 |

## 全局常量

| 属性 | 值 |
|-----|-----|
| **Background** | `#F8FAFC` |
| **Font Family** | `system-ui, -apple-system, PingFang SC, Microsoft YaHei, sans-serif` |
| **Font Size (Node)** | `13` |
| **Font Size (Edge Label)** | `12` |
| **Shape** | `rounded=1`（所有矩形） |
| **Edge Style** | `edgeStyle=orthogonalEdgeStyle`（正交连线） |
| **Edge Width** | `strokeWidth=2` |
| **Edge Color** | `#94A3B8` |
| **Label BG** | `labelBackgroundColor=#F8FAFC` |
| **Shadow** | `shadow=1`（所有节点） |

## XML 基础模板

```xml
<?xml version="1.0" encoding="UTF-8"?>
<mxfile host="app.diagrams.net" modified="2025-01-01T00:00:00.000Z" agent="drawio-chart" version="24.0.0">
  <diagram name="Page-1" id="diagram-id">
    <mxGraphModel dx="1422" dy="794" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="827" pageHeight="1169" math="0" shadow="0" background="#F8FAFC">
      <root>
        <mxCell id="0"/>
        <mxCell id="1" parent="0"/>
        <!-- 图表内容 -->
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>
```

## 节点模板速查

### 通用核心节点
```xml
<mxCell id="{id}" value="{标签}" style="rounded=1;whiteSpace=wrap;fillColor={填充色};strokeColor=none;fontColor={文字色};fontFamily=system-ui, -apple-system, PingFang SC, Microsoft YaHei, sans-serif;fontSize=13;shadow=1;" vertex="1" parent="1">
  <mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/>
</mxCell>
```

### 数据库节点（圆柱体）
```xml
<mxCell id="{id}" value="{标签}" style="shape=cylinder3;whiteSpace=wrap;fillColor={填充色};strokeColor=none;fontColor={文字色};fontFamily=system-ui, -apple-system, PingFang SC, Microsoft YaHei, sans-serif;fontSize=13;shadow=1;" vertex="1" parent="1">
  <mxGeometry x="{x}" y="{y}" width="120" height="80" as="geometry"/>
</mxCell>
```

### 菱形判断节点
```xml
<mxCell id="{id}" value="{条件}" style="rhombus;whiteSpace=wrap;fillColor=#005D7B;strokeColor=none;fontColor=#FFFFFF;fontFamily=system-ui, -apple-system, PingFang SC, Microsoft YaHei, sans-serif;fontSize=13;shadow=1;" vertex="1" parent="1">
  <mxGeometry x="{x}" y="{y}" width="120" height="80" as="geometry"/>
</mxCell>
```

### 分组容器（泳道）
```xml
<mxCell id="{id}" value="{标签}" style="swimlane;whiteSpace=wrap;fillColor=none;strokeColor=#005D7B;dashed=1;strokeWidth=2;fontColor=#2D3748;fontFamily=system-ui, -apple-system, PingFang SC, Microsoft YaHei, sans-serif;fontSize=13;" vertex="1" parent="1">
  <mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/>
</mxCell>
```

## 连线模板速查

### 标准连线（带标签）
```xml
<mxCell id="{id}" value="{标签}" style="edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;strokeWidth=2;strokeColor=#94A3B8;labelBackgroundColor=#F8FAFC;fontFamily=system-ui, -apple-system, PingFang SC, Microsoft YaHei, sans-serif;fontSize=12;fontColor=#64748B;" edge="1" source="{source-id}" target="{target-id}" parent="1">
  <mxGeometry relative="1" as="geometry"/>
</mxCell>
```

### 无标签连线
```xml
<mxCell id="{id}" value="" style="edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;strokeWidth=2;strokeColor=#94A3B8;" edge="1" source="{source-id}" target="{target-id}" parent="1">
  <mxGeometry relative="1" as="geometry"/>
</mxCell>
```

### 虚线连接（异步/间接）
```xml
<mxCell id="{id}" value="{标签}" style="edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;dashed=1;strokeWidth=2;strokeColor=#94A3B8;labelBackgroundColor=#F8FAFC;fontFamily=system-ui, -apple-system, PingFang SC, Microsoft YaHei, sans-serif;fontSize=12;fontColor=#64748B;" edge="1" source="{source-id}" target="{target-id}" parent="1">
  <mxGeometry relative="1" as="geometry"/>
</mxCell>
```

## 注意事项

1. **ID 唯一性**：每个 `mxCell` 的 `id` 必须唯一，建议用 `node-1`, `node-2`, `edge-1` 等命名
2. **XML 转义**：属性值中的 `&` `<` `>` `"` 必须转义
3. **注释禁止使用 `--`**：XML 注释中不能出现双连字符
4. **坐标网格对齐**：所有坐标按 10px 对齐
5. **节点尺寸**：通用节点 140×60，数据库 120×80，菱形 120×80，容器按内容定
6. **间距**：节点之间水平间距 ≥ 40px，垂直间距 ≥ 30px
