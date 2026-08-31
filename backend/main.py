import uvicorn


def main() -> None:
    uvicorn.run("wechat_bot.app:app", host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
