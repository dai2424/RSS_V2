1. Web界面上可以添加RSS订阅，并对RSS订阅的进行行业分类
2. 对于订阅源头做管理，启用停用，
3. RSS源要保存一定的元数据，可以参照RSS_SLOP
4. 对于英文RSS信息，可以使用agent服务来生产对应的中文信息
5. agent模块要求做对于api的管理, 目前使用的opencode go 的api套餐，使用 deepseekv4.1-flash 作为模型
   1. 这个要求可以配置多个apikey 有个号池与优先级的概念