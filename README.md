# 工位试排的尺寸短语与材料承诺

下料办公室的图样、可用料段及班组文字记录。数据仅供离线试排。材料中的人、机构及经营记录是合成的。

每份资料为 UTF-8 JSON 数组。id 在同一份资料内唯一，原始文字按收到的形式保留。

- `data/drawings.json`。图样与库存的业务释义。尺寸和容差用毫米，kerf 是每次切取一段的耗损。库存 direction 表示可用朝向，长余料以 min_reuse_mm 为下限；priority 越小越早处理。
- `data/stock.json`。棒料库存按杆料号区分。free 和 occupied 为从该杆基准端计的半开区间。locked_refs 是已约定的占用引用。
- `data/chat.json`。下料试排通话的文字导出。thread 内 seq 为会话顺序，jobs 表示议及的工单而非已决定的对象。source 是话轮角色。

使用 Python 3.11 标准库，无需安装软件包或连接外部服务。

以下命令分别读取一份原始资料、检查资料中的字段与关联。

```sh
python3 -m json.tool data/drawings.json
python3 -m unittest discover -s tests -v
```

## 离线试排工具 nestplan

`nestplan/` 把班组文字试排落成可确认的下料计划，全程只读合成资料、
只出试排单与确认单，不连接机床或任何外部服务。用隔离模式运行，避免从
工作目录或环境变量加载到非预期代码：

```sh
python3 -I run_tests.py                                   # 全部单元测试
python3 -I nestplan_cli.py report                         # 截至最后一条话轮的中文试排报告
python3 -I nestplan_cli.py report --seq 4                 # 回到任意话轮时点
python3 -I nestplan_cli.py requirements --seq 5           # 零件需求 JSON（带原句出处）
python3 -I nestplan_cli.py sheet --seq 5 --output sheet.json
python3 -I nestplan_cli.py verify sheet.json              # 确认单读回与占用一致性自检
```

### 落实的业务口径

- **原句可回溯**：每处数量、尺寸、对象、材料限制都带话轮 id/seq/角色与
  原文片段；解析不了的句式显式进“待澄清”，绝不静默吞字或只抽数字。
- **公差与留量分开**：`tolerance_mm` 只来自图样且不可被话轮修改；
  下料留量 `trim_mm` 单独记账，毛坯 = 成品 + 留量；一根杆放 n 段时
  总占用 = 毛坯之和 + (n-1) 次 kerf。留量不得低于图样 `trim_min_mm`。
- **单位**：毫米/公分/米/英寸（1 英寸 = 25.4mm）显式换算；图样
  `units_default` 为 null 的工单（R5）不接受裸数字；“毛/分”等口语
  单位只有在工单约定（CLI 里 `DEFAULT_SLANG_MM`）存在时才落实。
- **三类取料约束**：必须共料的切件组（同杆同段，整组确认/整组失效）、
  优先用余料料头的切件、允许任意相容库存的切件。
- **指代澄清**：“第二根”无法落实到唯一库存对象时，只阻塞实际议及的
  切件与试排单，无关切件继续试排；落实后记录原句与被否决的杆。
- **排料顺序**：同优先级内先消化料头杆、再已开杆、最后才动新杆；
  同等条件优先更短的自由段，保住可继续用的长余料（≥ `min_reuse_mm`）。
- **确认台账不可悄悄重排**：共料承诺变更时整组升版本、旧占用成对
  释放、新组重新确认；其他工单的已确认占用与库存 `locked_refs` 不动。
- **到货时序**：`arrives_after` 指向话轮 id，只有当前 seq 超过它杆才
  可用（S5 在 M5 之后才到货）。
- **休息恢复**：确认单保存 active 占用、完整释放/失效历史与原句引用，
  读回时做区间冲突自检；被取代的旧方案标注“曾可行、需重新问人”。

### 模块划分

| 模块 | 职责 |
| --- | --- |
| `nestplan/units.py` | 公制/英制/口语单位与裸数字约定 |
| `nestplan/statements.py` | 话轮切句、句式分类、原句保留 |
| `nestplan/requirements.py` | 零件需求、共料组、澄清项 |
| `nestplan/stock.py` | 棒料自由段/占用/locked 引用与到货时序 |
| `nestplan/planner.py` | 一维试排、缺料、余料与候选排序 |
| `nestplan/session.py` | 话轮回放、指代、确认台账与确认单 |
| `nestplan/report.py` | 中文试排报告渲染 |
| `nestplan/cli.py` | 命令行入口 |
