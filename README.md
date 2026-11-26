# 国内 App GKD 精选集成订阅

以现有 Lin-arm + 奥怪 v1 合并文件为固定基线，补充甘霖与梦念逍遥的应用级广告规则。
保持订阅 ID `2026112601` 和已有规则组编号，不替换或重编号已有规则。

## 来源

| 来源 | 用途 | 许可依据 |
| --- | --- | --- |
| [Lin-arm](https://github.com/Lin-arm/GKD_subscription) + [奥怪](https://github.com/aoguai/subscription) | 保留已发布 v1 基线 | 保留原快照的署名和条款；奥怪为 GPL-3.0 |
| [甘霖](https://github.com/ganlinte/GKD-subscription) | 补充应用级广告匹配 | 仓库 LICENSE：MIT |
| [梦念逍遥](https://github.com/MengNianxiaoyao/gkd-subscription) | 补充应用级广告匹配，包括闲鱼开屏 | package.json 声明 MIT，无独立 LICENSE 文件 |

原许可文件和许可声明保存在 `licenses/`，不对全部上游内容重新授权。
`sources.json` 记录真实订阅地址及来源顺序；基线固定到已发布提交，新增来源按配置顺序优先保留。

## 合并边界

- 新来源只接收名称以「开屏广告／全屏广告／局部广告／分段广告」开头的应用规则组。
- 默认只开启开屏广告；其他新增广告组保留，但默认关闭。上游明确禁用的开屏组不导入。
- 不新增功能类自动化；新增广告组中发现登录、授权、支付等名称或明确同意／付款按钮时也排除。
- 保留基线全局规则，不叠加新来源的全局扫描。
- 相同页面、选择器、动作和版本条件的独立规则去重；轮询、延迟、冷却差异不作为新增覆盖。
- 含前置步骤、共享冷却／次数或跨组引用的动作链保持完整；跨组编号整体映射。
- 动作链缺少依赖、依赖非广告组或默认关闭组时，不导入该组，并在报告中记录原因。
- 不推断不同选择器是否语义等价；不以名称相同、编号相同作为删除依据。

`integration-report.json` 记录来源版本、内容哈希、实际新增数量、去重数量和跳过原因。
报告中的新增仅表示规则覆盖，不代表在每个手机与 App 版本上均已实测成功。

## 本地构建

需要 Python 3。依赖安装到仓库本地目录，不修改系统 Python：

```bash
python3 -m pip install --target .deps -r requirements.txt
PYTHONPATH=.deps python3 merge_subscriptions.py
```

输出为根目录 `gkd-merged.json5`，订阅地址无需因本地文件名变化而迁移。
内容变化时自动递增订阅版本；内容未变时保持版本。该脚本不执行 Git 提交或推送。
没有定时更新工作流，需要主动运行构建并发布。

现有公开地址：

```text
https://raw.githubusercontent.com/zhang9958/my_gkd_subscribe/main/gkd-merged.json5
```

只有将本地结果推送到 GitHub 后，线上订阅才会更新。不要同时开启原始来源订阅与本合并订阅。

## 验证

```bash
PYTHONPATH=.deps python3 -m unittest discover -s tests -v
```
