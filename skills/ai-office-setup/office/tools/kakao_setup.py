# 카카오톡 연결 도구 (처음 한 번, 그리고 두 달쯤 지나 로그인이 만료됐을 때)
# 비서 아침 보고를 교수님 카톡(나에게 보내기)으로 받으려면 카카오 로그인이 한 번 필요해요.
# 입력한 키와 로그인 정보는 data/kakao.json 에만 저장되고 깃허브에는 올라가지 않아요.
# 사용: python3 -X utf8 tools/kakao_setup.py
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))
import kakao  # noqa: E402

REDIRECT = "https://localhost"


def main():
    old = {}
    if kakao.ready():
        try:
            old = kakao.load()
        except kakao.KakaoError:
            old = {}
    print("카카오톡 '나에게 보내기'를 연결해요.\n")
    print("준비: developers.kakao.com → 내 애플리케이션 → (앱 선택)")
    print("  · 카카오 로그인: 켜기, Redirect URI 등록 (예: %s — 이미 등록된 주소가 있으면 그걸 써도 돼요)" % REDIRECT)
    print("  · 동의항목: '카카오톡 메시지 전송(talk_message)' 켜기")
    print("  · 앱 키: 'REST API 키' 복사\n")
    key = input("REST API 키%s: " % (" (엔터 = 전에 쓰던 키)" if old.get("rest_api_key") else "")).strip() or old.get("rest_api_key", "")
    if not key:
        print("키가 없어요. 다시 실행해 주세요.")
        sys.exit(1)
    secret = input("Client Secret (안 쓰면 그냥 엔터): ").strip() or old.get("client_secret", "")
    redirect = input("앱에 등록한 Redirect URI (엔터 = %s): " % (old.get("redirect_uri") or REDIRECT)).strip() \
        or old.get("redirect_uri") or REDIRECT
    url = "https://kauth.kakao.com/oauth/authorize?" + urllib.parse.urlencode(
        {"client_id": key, "redirect_uri": redirect, "response_type": "code", "scope": "talk_message"})
    print("\n1) 아래 주소를 브라우저에서 열고 카카오 로그인·동의를 해 주세요:\n\n" + url + "\n")
    print("2) 로그인이 끝나면 '사이트에 연결할 수 없음' 화면이 나와요(정상이에요).")
    print("   그때 주소창의 주소(?code=... 가 들어 있음)를 통째로 복사해서 붙여 주세요.\n")
    pasted = input("주소: ").strip()
    code = urllib.parse.parse_qs(urllib.parse.urlparse(pasted).query).get("code", [pasted])[0]
    fields = {"grant_type": "authorization_code", "client_id": key, "redirect_uri": redirect, "code": code}
    if secret:
        fields["client_secret"] = secret
    try:
        tok = kakao._post(kakao.TOKEN_URL, fields)
    except kakao.KakaoError as ex:
        print("\n로그인 정보를 받지 못했어요: %s" % ex)
        print("주소를 다시 받아서(1번부터) 해 주세요. 코드는 한 번만 쓸 수 있어요.")
        sys.exit(1)
    conf = {"rest_api_key": key, "client_secret": secret, "redirect_uri": redirect, "public_url": old.get("public_url", "")}
    kakao.save(kakao.apply_token(conf, tok))
    link = input("\n사무실 주소(휴대폰에서 여는 https://...ts.net 주소, 없으면 엔터): ").strip() or conf["public_url"]
    if link != conf["public_url"]:
        conf["public_url"] = link
        kakao.save(conf)
    try:
        kakao.send_text("✅ AI 파트너스 사무실과 카톡이 연결됐어요. 내일부터 비서 아침 보고가 여기로 와요.", link)
        print("\n연결됐어요! 카톡 '나와의 채팅'에 시험 메시지를 보냈어요.")
    except kakao.KakaoError as ex:
        print("\n로그인은 됐지만 시험 메시지를 보내지 못했어요: %s" % ex)
        print("동의항목에서 '카카오톡 메시지 전송'이 켜져 있는지, 사무실 주소가 앱의 '플랫폼 → Web 사이트 도메인'에 등록됐는지 확인해 주세요.")


if __name__ == "__main__":
    main()
