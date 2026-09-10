# Compose 开发环境

从项目根目录运行。`docker-compose.yml` 使用镜像内的代码；`compose.dev.yaml` 是显式开发覆盖，不会被默认自动加载。沿用原项目名、私网、共享 `gateway` 网络和数据卷，不创建第二套数据库。切换前确认 `.env` 的 `COMPOSE_PROJECT_NAME` 是目标开发项目。

```sh
docker compose -f docker-compose.yml -f compose.dev.yaml up -d --build
```

开发配置保留非 root 后端，并将后端根文件系统设为只读。SQLite 继续使用 `bot_data`，临时文件使用 `/tmp`。只读挂载 `backend/wechat_bot`、`scripts`、`tests` 和 `main.py`，保留镜像内的 Linux `.venv`，不会用宿主机虚拟环境覆盖它。

- 修改 `backend/wechat_bot/*.py` 后，Uvicorn 自动重载。当前依赖下使用轮询检测，兼容 Docker Desktop 的目录挂载。
- 修改脚本或测试后，下次执行即生效，不需要复制文件或重建镜像。
- 修改 Python 依赖、Dockerfile 或环境变量后，重新执行上面的构建启动命令。
- 前端保持一次性构建语义，不启动 Vite 开发服务。源码、静态资源、HTML 入口和构建配置已挂载，依赖仍在镜像内。修改前端后运行下面的命令更新共享静态产物，再刷新浏览器：

```sh
docker compose -f docker-compose.yml -f compose.dev.yaml run --rm --no-deps frontend-builder
```

修改前端 `package.json` 或锁文件时，先重新构建 `frontend-builder` 镜像。Nginx 配置以只读方式挂载，修改后检查并重载：

```sh
docker compose exec -T gateway nginx -t
docker compose exec -T gateway nginx -s reload
```

## 运行脚本与测试

容器工作目录是 `/app`，用相对路径运行脚本，PowerShell 和 Git Bash 都适用：

```sh
docker compose exec -T backend python scripts/test_wechat_kf_api.py --list-customers --database data/wechat_bot.db
docker compose exec -T backend python scripts/test_wechat_kf_api.py --list-customers
docker compose exec -T backend .venv/bin/python -m unittest discover -s tests
```

第一条读取数据库中的历史客户，不调用企业微信；数据库路径若通过 `DATABASE_PATH` 改过，需对应修改。第二条列出企业微信最近消息中的客户。不加 `--send-test` 不会发送消息。

脚本通过容器已有的环境变量获取企业微信配置，不需要挂载 `.env`。开发配置用于 `up`、`run` 等创建容器的操作；`exec` 和 `logs` 操作已存在的容器，可省略开发覆盖参数。

Git Bash/MSYS 会自动将命令行里的 `/app/...` 转成 Windows 路径，导致 `C:/Program Files/Git/app/...` 错误。优先使用上述相对路径。必须使用绝对路径时，在 Git Bash 中关闭本次命令的路径转换：

```bash
MSYS_NO_PATHCONV=1 docker compose exec -T backend python /app/scripts/test_wechat_kf_api.py --list-customers --database /app/data/wechat_bot.db
```

## 切回镜像运行

```sh
docker compose -f docker-compose.yml up -d --build --force-recreate
```

这会移除开发挂载和 reload，使用镜像内代码。数据库卷保留，无需执行 `down -v`。开发期间执行不带覆盖文件的 `up` 也可能切回镜像配置，因此开发启动时始终显式传入两个 `-f`。
