# 贡献指南

本仓库为课程作业项目，同时按开源项目习惯组织，欢迎以 Issue 与 Pull Request 的方式提出改进。

## 一、开始之前

1. 先阅读 [README.md](README.md) 与 [docs/design.md](docs/design.md)，了解项目边界。
2. 业务规则以 `docs/01-业务挖掘报告.md` 中的事实编号（fXX）为依据，新增规则请注明引用的编号。
3. 涉及扣罚、合格结论、复查结论的需求不在本项目范围内，请勿提交。

## 二、开发流程

```bash
git checkout -b feat/your-feature
pip install -r requirements.txt
pytest -q
git add .
git commit -m "feat: 简短说明"
git push origin feat/your-feature
```

然后在 GitHub 上发起 Pull Request，并在描述中说明：

- 改了什么、为什么改
- 对应的事实编号或 Issue
- 如何验证（命令或截图）

## 三、代码规范

- Python 3.10+，遵循 PEP 8，函数保持单一职责。
- 规则、阈值等可调参数放 `config/default.yaml`，不要硬编码在业务逻辑里。
- 新增公共函数需要同步补测试，测试放在 `tests/` 下并与 `src/` 结构对应。
- 中文注释用于解释“为什么”，不要重复代码字面意思。

## 四、提交信息

采用 Conventional Commits：

| 前缀 | 含义 |
| --- | --- |
| feat | 新功能 |
| fix | 修复缺陷 |
| docs | 文档变更 |
| test | 测试相关 |
| refactor | 重构 |
| chore | 构建、依赖等杂项 |

## 五、文档变更

交付物（docx / xlsx / 图片）更新后，请同步修改 `CHANGELOG.md`，并确认 `README.md` 中的链接仍然有效。
