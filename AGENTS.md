ILANG
[TYPE:agent-policy][PROJECT:vps-deals][LANG:zh]

::STATE{@PROJECT, purpose:自动汇总公开且可核验的VPS价格与优惠, runtime:纯Python静态站, hosting:Cloudflare Pages}

::MODULE{SOURCE_OF_TRUTH}
  [MUST] .ilang/site.ilang 是品牌 厂商 抓取入口 字段 渲染和更新频率的唯一配置源
  [MUST] scraper.py 与 build.py 必须通过 ilang_config.py 读取该文件
  [MUST] data/offers.json 中每条记录保留 source_url 与 fetched_at

::MODULE{ALLOWED}
  [ALLOW] 增删官方公开数据源 改进保守解析器 修复模板 SEO 可访问性 测试和构建
  [ALLOW] 增加公开联盟链接 但必须保留披露且遵守平台条款
  [ALLOW] 调整页面设计 但不能隐藏来源 时间或价格限定条件

::MODULE{DATA_BOUNDARY}
  [NEVER] 编优惠 编价格 编截止日期 编佣金 编用户评价
  [NEVER] 绕反爬 绕登录 绕robots.txt 注入cookie 品牌词竞价 买粉刷量
  [MUST] 页面解析失败时保留来源状态 不用旧价格伪装成刚抓到的新价格
  [MUST] 金额只有在官方来源明确出现时才能进入Offer结构化数据

::MODULE{OPERATIONS}
  [MUST] 变更后运行 python scraper.py 再运行 python build.py 和 python -m unittest discover -s tests
  [MUST] 发布前检查 site/index.html site/robots.txt site/sitemap.xml 与至少一个详情页
  [MUST] GitHub Actions 每6小时运行一次 只提交真实抓取与构建结果

::BOUNDARY{never:造假数据 刷量 收集访客隐私 运行时调用付费推理|scope:permanent}
