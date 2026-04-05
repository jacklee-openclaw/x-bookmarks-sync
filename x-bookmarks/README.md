# X Bookmarks Repository

## 1) 仓库用途
本目录用于保存和整理从 X（Twitter/X）同步出的书签内容，目标是：

- 把原始抓取结果和可读内容分离
- 把“待整理”和“可长期保留”分离
- 保留历史结构和错误同步痕迹，避免数据不可追溯

该目录只存放与书签归档相关的数据，不放运行时数据库和临时缓存。

## 2) 目录结构

```text
x-bookmarks/
  README.md
  raw/
    oembed/
  inbox/
    retry/
  curated/
    x/
  archive/
    legacy-YYYY-MM-DD/
  assets/
  scripts/
  metadata/
```

- `raw/`: 原始同步输出（JSON）。只做保存，不做人工编辑。
- `inbox/`: 待处理内容（例如抓取失败、正文异常、分类待确认）。
- `curated/`: 已整理、可长期保留内容。
- `archive/`: 历史结构、旧文件、可疑同步结果的归档区。
- `assets/`: 图片、附件等静态资源（如后续需要）。
- `scripts/`: 与 `x-bookmarks` 本目录直接相关的脚本（可选）。
- `metadata/`: 索引、清理日志、映射表、维护记录。

## 3) 工作流

1. 同步工具先落 `raw/`。
2. 解析后进入 `inbox/`（待人工或规则整理）。
3. 确认有效后移动到 `curated/`。
4. 旧结构、历史噪声、误同步数据统一进入 `archive/`。
5. 每次结构调整，在 `metadata/` 留下清理记录。

## 4) 命名和维护约定

- `curated/x/` 下文件名优先使用 `tweet_id.md`（避免超长文件名和非法字符问题）。
- 同一条书签只保留一个主版本；旧版放 `archive/`。
- 非 X 来源（如微信、测试页）不进入 `curated/`。
- 运行时文件（如 sqlite、临时缓存）不进入该目录的长期版本管理。

## 5) Git 使用建议

常用命令：

```bash
# 查看变更摘要
git status --short
git diff --stat

# 仅提交 x-bookmarks 目录
git add x-bookmarks
git commit -m "chore: clean bookmark sync structure and rewrite README"
git push origin main
```

提交前建议检查：

- `curated/` 是否只包含可长期保留内容
- `inbox/` 是否存在需要后续处理的条目
- `archive/` 是否完整保留了迁移痕迹
