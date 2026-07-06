# Skills

个人 AI Agent 技能集合。每个技能自包含，独立可用。

## 技能列表

| 技能                                    | 简介                                                            |
| --------------------------------------- | --------------------------------------------------------------- |
| [chigua](./skills/chigua)               | 私人关系顾问 — 从吐槽中构建人际关系图谱，提供个性化社交应对建议 |
| [fund-briefing](./skills/fund-briefing) | 基金持仓简报 — 蚂蚁财富 PDF 解析 + 国际财经解读 + 持仓关联分析  |

## 目录结构

```
skills/
├── skills/
│   ├── chigua/
│   │   ├── SKILL.md          # 技能定义
│   │   ├── templates/        # 数据模板
│   │   └── references/       # 参考文档
│   └── fund-briefing/
│       ├── SKILL.md
│       ├── scripts/          # PDF 解析脚本
│       └── templates/        # 数据模板
└── README.md
```

每个技能以 `SKILL.md` 为入口（YAML frontmatter + 指令正文），自包含运行所需的所有脚本和模板。

## 备注

技能规范参考 [anthropics/skills](https://github.com/anthropics/skills)。
