import argparse, getpass, json
from app.main import create_user

p = argparse.ArgumentParser(description="Управление пользователями To-Do")
sub = p.add_subparsers(dest="command", required=True)
a = sub.add_parser("create-user")
a.add_argument("username")
a.add_argument("--password-file")
b = sub.add_parser("bootstrap")
b.add_argument("file", help="JSON-файл с массивом {username,password}")
args = p.parse_args()
if args.command == "create-user":
    password = (
        open(args.password_file, encoding="utf-8").read().rstrip("\r\n")
        if args.password_file
        else getpass.getpass("Пароль: ")
    )
    create_user(args.username, password)
    print("Пользователь создан")
else:
    with open(args.file, encoding="utf-8-sig") as f:
        users = json.load(f)
    if len(users) != 3:
        raise ValueError("Файл bootstrap должен содержать ровно трёх пользователей")
    for u in users:
        create_user(u["username"], u["password"])
    print("Пользователи созданы")
