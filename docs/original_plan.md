# AI无人机机群对战科研实践课题规划

AI 无人机机群对战科研实践课题，用 Openclaw 操控 Crazyflie 无人机机群，进行类似于 “反恐精英（Counter Strike）” 那样的 agent team 对战，不仅包括线下的无人机实战，而且也包括线上的仿真对战，通过线上仿真对战，优化无人机机群的协同战术。

围绕线下无人机机群对战这个主题，让同学们学习掌握多种技能，

- 智能设备 crazyflie 无人机的开发，

- Openclaw 与智能设备的通信，

- 图像识别与 VLA 模型（Vision-Language-Action），

- Openclaw 与 Unity 引擎的整合，

- Openclaw 控制 crazyflie 无人机机群，通过强化学习，优化协同战术，

该实践课题线下部分共 5 天，每天一个主题，同学们分组开展实践，带队老师提供集中讲解和科研指导。每个主题的实践，分为三档技能，基础技能、进阶技能、卓越技能。在保障每位同学都能掌握基础技能的前提下，在实践中让同学们，发现自己更适合向哪个方向发展。鼓励同学们挑战自我，设立高标准的baseline！


&nbsp;
## 第一天：Crazyflie 无人机的开发与调试

### 1. 课题描述

实现 crazyflie 无人机的基础开发与调试，确保无人机能正常启动、连接并响应基础指令，为后续 openclaw 操控，奠定硬件基础。工作流程如下：1. 搭建 crazyflie 和 ESP-IDF 开发环境；2. 熟悉 crazyflie 硬件结构（飞控、传感器、无线通信模块）的拆装；3. 编译并烧录基础固件到 crazyflie 无人机本体；4. 使用 python 编程，连接无人机，接收飞行参数（高度、速度、方向），控制无人机的运动。

### 2. 课题目的

以 crazyflie 为载体，学习嵌入式智能设备的开发。

### 3. 验收评分

- **基础技能（60分）**：安装crazyflie开发环境，使用cfclient成功连接无人机，无人机能正常起降；完成基础悬停调试，悬停时间不低于10秒，无明显漂移。

- **进阶技能（80分）**：自行编写 python 程序，实现无人机在虚拟围栏的有限空间中，进行起飞降落，前进后退，升降转弯，加速减速等飞行动作。

- **卓越技能（100分）**：安装ESP-IDF开发环境，在crazyflie无人机上，加装摄像头和wifi通信模组，并在电脑上看到实时回传的视频。


&nbsp;
## 第二天：Openclaw 与Crazyflie 无人机的双向通信

### 1. 课题描述

通过定制开发 openclaw plugin/node，实现 openclaw AI agent 与 crazyflie 无人机的双向通信。工作流程如下：1. 安装 openclaw AI agent
 系统；2. 在 openclaw 中加装微信和钉钉通信渠道；3. 开发 Openclaw plugin/node，打通从 openclaw gateway 到 crazyflie 无人机的双向通信链路；4. 在 openclaw 中创建多个 agents，每一个 agent 对应一架 crazyflie 无人机。

### 2. 课题目的

学习 Openclaw 与智能硬件（crazyflie 无人机）的对接。

### 3. 验收评分

- **基础技能（60分）**：成功安装 openclaw 系统，启动 openclaw gateway；把 openclaw 对接微信或钉钉等通信渠道。

- **进阶技能（80分）**：通过开发 openclaw plugin/node，实现 openclaw gateway 与 crazyflie 无人机的双向通信。

- **卓越技能（100分）**：在 openclaw 中创建多个 agents，每一个 agent 对应一架 crazyflie 无人机。


&nbsp;
## 第三天：图像识别与 VLA 模型

### 1. 课题描述

通过调用图像识别与 VLA（Vision-Language-Action）模型，让无人机能通过摄像头识别环境目标，如 “恐怖份子” 与 “营救目标”），并将视觉信息转化为 openclaw/crazydrone 可执行的动作指令，实现“视觉-语言-动作”的联动。工作流程如下：1. 编写 skill.md，指挥 openclaw 调用图像识别模型，识别 “恐怖份子” 与 “营救目标”；2. 编写第二个 skill.md，指挥 openclaw 调用 LLM，把识别结果转化为战术目标，如 “追击恐怖份子” “前往目标地点”；3. 编写第三个 skill.md，把抽象的战术目标，分解为 crazyflie 的飞行姿态操控指令，如 “前进 10 米，高度从 2 米匀速爬升到 5 米”。

### 2. 课题目的

Openclaw 调用多种 AI 模型，并编写 skill.md，动态组织工作流程。

### 3. 验收评分

- **基础技能（60分）**：编写 skill.md，指挥 openclaw 调用图像识别模型，识别 “恐怖份子” 与 “营救目标”。

- **进阶技能（80分）**：编写第二个和第三个 skill.md，指挥 openclaw 调用 LLM，把识别结果转化为战术目标，如 “追击恐怖份子” “前往目标地点”，然后把抽象的战术目标，分解为 crazyflie 的飞行姿态操控指令，如 “前进 10 米，高度从 2 米匀速爬升到 5 米”。

- **卓越技能（100分）**：从图像视频的采集，到图像识别，到战术目标的制定，到飞行动作的分解，到 crazyflie 无人机的实际运动，完成全链路的闭环。


&nbsp;
## 第四天：Openclaw 与 Unity Engine

### 1. 课题描述

实现 openclaw 与 unity 引擎的通信，搭建 “线下 crazyflie 无人机对战“ + ”线上 unity 可视化仿真引擎” 的镜像系统。工作流程如下：1. 安装unity 引擎，安装开源的 FPS 对战系统；2. 开发 openclaw plugin/node，实现 openclaw 与 unity 引擎的通信；3. 接通 Command downlink 信道： Human player (微信或钉钉) -> Openclaw gateway -> openclaw agent -> (1) openclaw plugin for crazyflie -> crazyflie drone (2) openclaw plugin for unity -> unity FPS game；4. 接通 Telemetry feedback uplink 信道: crazyflie drone ->  openclaw plugin for crazyflie -> openclaw agent -> openclaw plugin for unity -> unity FPS game。

### 2. 课题目的

学习 openclaw 与软件（unity engine）的对接。

### 3. 验收评分

- **基础技能（60分）**：成功安装 unity 引擎，并安装 open-source FPS 对战。

- **进阶技能（80分）**：开发 openclaw plugin/node，对接 unity engine。

- **卓越技能（100分）**：实现 downlink 和 uplink 双向通信的全链路闭环。


&nbsp;
## 第五天：无人机机群协同与强化学习

### 1. 课题描述

实现Openclaw控制Crazyflie无人机机群的协同作战，并通过强化学习，优化机群协同战术。工作流程如下：1. 配置多台 crazyflie 无人机，实现 crazyflie swarm；2. 在同一套 openclaw gateway 中设置多个 agents，每个 agent 对应一个 crazyflie 无人机；3. 实现简单的 crazyflie 无人机机群协同；4. 通过 Multi-agent Reinforcement Learning (MARL)，在 unity engine 仿真环境中，优化 crazyflie swarm 的战术协同。

### 2. 课题目的

学习 openclaw 多个 agents 的协作，学习强化学习。

### 3. 验收评分

- **基础技能（60分）**：配置多台 crazyflie 无人机，实现 crazyflie swarm。

- **进阶技能（80分）**：在同一套 openclaw gateway 中设置多个 agents，每个 agent 对应一个 crazyflie 无人机，并实现简单的 crazyflie 无人机机群协同。

- **卓越技能（100分）**：学习 Multi-agent Reinforcement Learning (MARL) 算法，在 unity engine 仿真环境中，优化 crazyflie swarm 的战术协同。