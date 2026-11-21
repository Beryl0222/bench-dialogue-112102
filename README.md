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
