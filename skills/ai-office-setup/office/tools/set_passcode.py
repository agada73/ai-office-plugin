# 접속 비밀번호 정하기 도구
# 다른 컴퓨터(윈도우 노트북 등)에서 사무실을 열 때 쓰는 비밀번호를 정합니다.
# 입력한 비밀번호는 화면에 보이지 않고, 대화창이나 깃허브에도 올라가지 않습니다(data/settings.json 에만 저장).
# 사용: python3 -X utf8 tools/set_passcode.py
import getpass
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "data", "settings.json")


def main():
    print("다른 컴퓨터에서 사무실을 열 때 쓸 접속 비밀번호를 정해요.")
    print("(입력해도 화면에는 아무것도 안 보이는 게 정상이에요. 8글자 이상으로 해 주세요.)\n")
    a = getpass.getpass("새 접속 비밀번호: ")
    if len(a) < 8:
        print("\n8글자보다 짧아요. 다시 실행해 주세요.")
        sys.exit(1)
    b = getpass.getpass("한 번 더 입력: ")
    if a != b:
        print("\n두 번 입력한 비밀번호가 달라요. 다시 실행해 주세요.")
        sys.exit(1)
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    data = {}
    if os.path.exists(PATH):
        try:
            data = json.load(open(PATH, encoding="utf-8"))
        except ValueError:
            data = {}
    data["passcode"] = a
    data["remote"] = True
    with open(PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.chmod(PATH, 0o600)
    print("\n✓ 접속 비밀번호를 저장했어요. 원격 접속이 켜졌어요.")
    print("  사무실이 새 설정으로 시작되도록 아래 명령을 실행해 주세요:")
    print("  launchctl kickstart -k gui/$(id -u)/kr.aioffice.office")


if __name__ == "__main__":
    main()
