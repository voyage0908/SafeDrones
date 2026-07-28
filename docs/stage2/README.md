# 阶段二：FastAPI Gateway + LLM Commander

## 目标

把阶段一的手动 MQTT 指令替换为 HTTP 网关：

1. `POST /api/command` 接收自然语言命令
2. 默认调用 DeepSeek，把文本转成结构化航点 JSON
3. 网关发布到 `swarm/drone/{id}/command`
4. 网关订阅 `swarm/commander/status` 和 `swarm/commander/override`，供后续 Safety Gate 使用
5. 反向事件默认写入 `logs/gateway_events.jsonl`，同时保留最近事件用于 `/api/events` 和后续 LLM 上下文

## API Key

不要把 API Key 提交到 Git。网关按以下顺序读取：

1. `LLM_API_KEY`
2. `DEEPSEEK_API_KEY`
3. `DEEPSEEK_API_KEY_FILE` 指向的文件，默认是项目根目录下的 `deepseek_api_key`

`deepseek_api_key` 已加入 `.gitignore`。

## 模型 Provider

默认：

```bash
export LLM_PROVIDER=deepseek
export DEEPSEEK_MODEL=deepseek-v4-flash
```

其它 OpenAI-Compatible 服务：

```bash
export LLM_PROVIDER=openai_compatible
export LLM_BASE_URL=https://your-provider.example.com
export LLM_MODEL=your-model
export LLM_API_KEY=your-key
```

无网络开发兜底：

```bash
export LLM_PROVIDER=heuristic
```

单独检查当前模型 Provider：

```bash
python scripts/check_llm.py "让 1 号无人机去左前方侦察点"
```

## 启动顺序

终端 1：

```bash
conda activate eai-swarm
python scripts/dev_broker.py
```

终端 2：

```bash
conda activate eai-swarm
python mock_drone.py --drone-id 1
```

终端 3：

```bash
conda activate eai-swarm
uvicorn gateway:app --host 127.0.0.1 --port 8000
```

发送自然语言命令：

```bash
curl -X POST http://127.0.0.1:8000/api/command \
  -H 'Content-Type: application/json' \
  -d '{"text":"让 1 号无人机去左前方侦察点","drone":1}'
```

无 LLM 的直接命令接口：

```bash
curl -X POST http://127.0.0.1:8000/api/direct-command \
  -H 'Content-Type: application/json' \
  -d '{"drone":1,"waypoint":[5,5,2]}'
```

查看反向事件：

```bash
curl http://127.0.0.1:8000/api/events
```

事件日志默认位置：

```bash
logs/gateway_events.jsonl
```

可以用环境变量覆盖：

```bash
export MQTT_EVENT_LOG=/tmp/gateway_events.jsonl
```
