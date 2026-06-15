import secrets
import requests
import jwt
import os
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import FastAPI, HTTPException, Request, Depends, Form
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel
import uvicorn
import shelve
import atexit

# ========== 硬编码配置 ==========
WX_APPID = "你的小程序" #<-需要修改 1
WX_SECRET = "你的小程序密钥"#<-------------------------------需要修改 2 
JWT_SECRET = " 自己顺便设置个字符串别太短" #需要修改https://yc2019.cn/pages/tools/GenStr.html
JWT_EXPIRE_MINUTES = 60 * 24 * 7

# OAuth 2.0 客户端配置（与 Casdoor 中填写的 client_id/secret 一致）
OAUTH_CLIENTS = {
    "Casdoor客户端名称": {#<-------------------------------需要修改 3 
        "client_secret": "Casdoor客户端密钥",# 4 <------------需要修改
        "redirect_uris": ["https://你的Casdoor地址/callback"] #5 <-----------需要修改 
    }
}"

# 小程序码配置
WXACODE_PAGE = "pages/index/index"      # 小程序中处理登录的页面路径
WXACODE_WIDTH = 280                     # 二维码宽度（280~1280）
WXACODE_CACHE_DIR = "./qrcode_cache"    # 缓存目录，避免重复调用微信接口

# ========== 持久化存储（shelve） ==========
def get_sessions():
    return shelve.open("data_sessions.db", writeback=True)

def get_openid_to_session():
    return shelve.open("data_openid_to_session.db", writeback=True)

def get_auth_codes():
    return shelve.open("data_auth_codes.db", writeback=True)

atexit.register(lambda: get_sessions().close())
atexit.register(lambda: get_openid_to_session().close())
atexit.register(lambda: get_auth_codes().close())

app = FastAPI(title="OAuth 2.0 授权服务器（扫码自动登录）")
security = HTTPBearer()

# ========== 微信小程序码生成器（封装） ==========
class WechatMiniProgram:
    def __init__(self, appid, secret):
        self.appid = appid
        self.secret = secret
        self.access_token = None
        self.token_expires_at = 0

    def get_access_token(self):
        if self.access_token and time.time() < self.token_expires_at:
            return self.access_token
        url = "https://api.weixin.qq.com/cgi-bin/token"
        params = {"grant_type": "client_credential", "appid": self.appid, "secret": self.secret}
        resp = requests.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        if "access_token" not in data:
            raise Exception(f"获取access_token失败: {data}")
        self.access_token = data["access_token"]
        expires_in = data.get("expires_in", 7200) - 300
        self.token_expires_at = time.time() + expires_in
        return self.access_token

    def generate_wxacode(self, scene, page, width=280, save_path=None):
        token = self.get_access_token()
        url = f"https://api.weixin.qq.com/wxa/getwxacodeunlimit?access_token={token}"
        payload = {
            "scene": scene,
            "page": page,
            "width": width,
            "auto_color": False,
            "line_color": {"r": 0, "g": 0, "b": 0},
            "is_hyaline": False
        }
        headers = {"Content-Type": "application/json"}
        resp = requests.post(url, data=json.dumps(payload), headers=headers)
        resp.raise_for_status()
        if "image" in resp.headers.get("Content-Type", ""):
            if save_path:
                os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
                with open(save_path, "wb") as f:
                    f.write(resp.content)
                return save_path
            return resp.content
        else:
            error_data = resp.json()
            raise Exception(f"生成小程序码失败: {error_data}")

wx_mp = WechatMiniProgram(WX_APPID, WX_SECRET)

# ========== 辅助函数 ==========
def get_openid_by_code(code: str) -> str:
    url = "https://api.weixin.qq.com/sns/jscode2session"
    params = {
        "appid": WX_APPID,
        "secret": WX_SECRET,
        "js_code": code,
        "grant_type": "authorization_code"
    }
    resp = requests.get(url, params=params)
    data = resp.json()
    if "openid" not in data:
        raise HTTPException(400, f"微信错误: {data.get('errmsg', '未知')}")
    return data["openid"]

def create_jwt(openid: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MINUTES)
    payload = {"sub": openid, "exp": expire, "iat": datetime.now(timezone.utc)}
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

def verify_jwt(token: str) -> str:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])["sub"]
    except:
        raise HTTPException(401, "无效 token")

def generate_auth_code(openid: str) -> str:
    code = secrets.token_urlsafe(32)
    with get_auth_codes() as db:
        db[code] = {
            "openid": openid,
            "expires_at": datetime.now(timezone.utc) + timedelta(minutes=10)
        }
    return code

# ========== 微信小程序绑定接口（供小程序调用） ==========
class BindSessionRequest(BaseModel):
    code: str
    session_id: str

@app.post("/api/bind_session")
async def bind_session(req: BindSessionRequest):
    print(f"[绑定] 收到 session_id: {req.session_id}")
    openid = get_openid_by_code(req.code)
    print(f"[绑定] 微信 openid: {openid}")

    with get_sessions() as db:
        sess = db.get(req.session_id)
        if not sess or sess["status"] != "pending":
            print(f"[绑定] 错误: 会话不存在或状态不是 pending -> {sess}")
            raise HTTPException(400, "无效或已使用的会话ID")
        sess["status"] = "bound"
        sess["openid"] = openid
        db[req.session_id] = sess

    with get_openid_to_session() as db2:
        db2[openid] = req.session_id

    access_token = create_jwt(openid)
    print(f"[绑定] 成功: session_id={req.session_id} 绑定 openid={openid}")
    return {"access_token": access_token, "token_type": "bearer"}

# ========== 查询会话状态（供前端轮询） ==========
@app.get("/oauth/session_status/{session_id}")
async def session_status(session_id: str):
    with get_sessions() as db:
        sess = db.get(session_id)
        if not sess:
            return {"status": "not_found"}
        return {"status": sess["status"]}   # pending 或 bound

# ========== 生成小程序码图片（动态生成并缓存） ==========
@app.get("/oauth/qrcode_img/{session_id}")
async def get_qrcode_img(session_id: str):
    """返回小程序码图片（PNG），优先从缓存读取，否则调用微信接口生成"""
    if len(session_id) > 32:
        raise HTTPException(400, "session_id 长度超过32字符，无法放入小程序码 scene")
    
    # 缓存文件路径
    cache_file = os.path.join(WXACODE_CACHE_DIR, f"{session_id}.png")
    if os.path.exists(cache_file):
        # 检查文件修改时间是否超过10分钟，若是则重新生成
        if time.time() - os.path.getmtime(cache_file) < 600:   # 10分钟
            with open(cache_file, "rb") as f:
                return Response(content=f.read(), media_type="image/png")
    
    # 调用微信接口生成
    try:
        img_data = wx_mp.generate_wxacode(
            scene=session_id,
            page=WXACODE_PAGE,
            width=WXACODE_WIDTH,
            save_path=None    # 返回 bytes
        )
        # 写入缓存
        os.makedirs(WXACODE_CACHE_DIR, exist_ok=True)
        with open(cache_file, "wb") as f:
            f.write(img_data)
        return Response(content=img_data, media_type="image/png")
    except Exception as e:
        raise HTTPException(500, f"生成小程序码失败: {str(e)}")

# ========== OAuth 2.0 授权端点（展示二维码 + 自动轮询） ==========
@app.get("/oauth/authorize")
async def authorize(
    client_id: str,
    redirect_uri: str,
    response_type: str = "code",
    state: Optional[str] = None,
    scope: Optional[str] = None
):
    client = OAUTH_CLIENTS.get(client_id)
    if not client:
        raise HTTPException(400, "无效的 client_id")
    if redirect_uri not in client["redirect_uris"]:
        raise HTTPException(400, "redirect_uri 不匹配")
    if response_type != "code":
        raise HTTPException(400, "仅支持 response_type=code")

    session_id = secrets.token_urlsafe(16)
    with get_sessions() as db:
        db[session_id] = {
            "status": "pending",
            "openid": None,
            "created_at": datetime.now(timezone.utc)
        }
    print(f"[授权] 生成 session_id: {session_id}，client_id={client_id}")

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>微信扫码登录</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; text-align: center; margin-top: 50px; background: #f7f7f7; }}
            .container {{ max-width: 400px; margin: 0 auto; background: white; padding: 30px; border-radius: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
            h2 {{ color: #333; margin-bottom: 20px; }}
            .session-id {{ font-size: 14px; color: #666; margin-bottom: 10px; word-break: break-all; }}
            .qrcode {{ margin: 20px auto; width: 280px; height: 280px; background: #fff; display: flex; justify-content: center; align-items: center; }}
            .qrcode img {{ width: 100%; height: 100%; }}
            .status {{ margin-top: 20px; padding: 10px; border-radius: 8px; background: #f0f0f0; color: #666; font-size: 14px; }}
            .loading {{ display: inline-block; width: 16px; height: 16px; border: 2px solid #ccc; border-top-color: #07c160; border-radius: 50%; animation: spin 0.8s linear infinite; vertical-align: middle; margin-right: 8px; }}
            @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
            .success {{ color: #07c160; font-weight: bold; }}
            .error {{ color: #fa5151; }}
        </style>
    </head>
    <body>
    <div class="container">
        <h2>微信扫码登录</h2>
        <div class="session-id">
            会话ID：<strong style="color: #333;">{session_id}</strong>
        </div>
        <div class="qrcode">
            <img id="qrcode_img" src="/oauth/qrcode_img/{session_id}" alt="加载中...">
        </div>
        <div class="status" id="status_text">
            <span class="loading"></span> 请使用微信扫描二维码登录
        </div>
    </div>
    <script>
        const sessionId = "{session_id}";
        const clientId = "{client_id}";
        const redirectUri = "{redirect_uri}";
        const state = "{state if state else ''}";
        let polling = null;

        async function checkStatus() {{
            try {{
                const resp = await fetch(`/oauth/session_status/${{sessionId}}`);
                const data = await resp.json();
                if (data.status === "bound") {{
                    if (polling) clearInterval(polling);
                    document.getElementById("status_text").innerHTML = '<span class="success">✓ 登录成功，正在跳转...</span>';
                    
                    // 使用表单提交，避免 fetch 重定向问题
                    const form = document.createElement('form');
                    form.method = 'POST';
                    form.action = '/oauth/authorize/submit';
                    const fields = {{
                        session_id: sessionId,
                        client_id: clientId,
                        redirect_uri: redirectUri,
                        state: state
                    }};
                    for (const [name, value] of Object.entries(fields)) {{
                        if (value) {{
                            const input = document.createElement('input');
                            input.type = 'hidden';
                            input.name = name;
                            input.value = value;
                            form.appendChild(input);
                        }}
                    }}
                    document.body.appendChild(form);
                    form.submit();
                }}
            }} catch (err) {{
                console.error("轮询出错", err);
            }}
        }}

        polling = setInterval(checkStatus, 2000);
        setTimeout(() => {{
            if (polling) {{
                clearInterval(polling);
                document.getElementById("status_text").innerHTML = '<span class="error">登录超时，请刷新页面重试</span>';
            }}
        }}, 5 * 60 * 1000);
    </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)
    
# ========== 授权提交接口（被前端自动调用，也可保留手动兼容） ==========
@app.post("/oauth/authorize/submit")
async def authorize_submit(
    request: Request,
    session_id: str = Form(...),
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    state: str = Form(default=None)
):
    print(f"[提交] 收到 session_id: {session_id}")
    with get_sessions() as db:
        sess = db.get(session_id)
        if not sess or sess["status"] != "bound":
            print(f"[提交] 错误: 会话不存在或未绑定 -> {sess}")
            return HTMLResponse(content="<h3>会话ID未绑定，请重新扫码登录</h3>", status_code=400)
        openid = sess["openid"]
        print(f"[提交] 绑定成功，openid={openid}")

    # 生成授权码并重定向
    code = generate_auth_code(openid)
    redirect_url = f"{redirect_uri}?code={code}"
    if state:
        redirect_url += f"&state={state}"
    print(f"[提交] 重定向到: {redirect_url}")
    return RedirectResponse(url=redirect_url)

# ========== OAuth 2.0 Token 端点 ==========
@app.post("/oauth/token")
async def token(
    grant_type: str = Form(...),
    client_id: str = Form(...),
    client_secret: str = Form(...),
    code: str = Form(...),
    redirect_uri: str = Form(None),
):
    client = OAUTH_CLIENTS.get(client_id)
    if not client or client["client_secret"] != client_secret:
        raise HTTPException(401, "无效的客户端凭证")
    if grant_type != "authorization_code":
        raise HTTPException(400, "仅支持 authorization_code 模式")

    with get_auth_codes() as db:
        code_info = db.get(code)
        if not code_info or code_info["expires_at"] < datetime.now(timezone.utc):
            raise HTTPException(400, "无效或过期的授权码")
        openid = code_info["openid"]
        del db[code]

    access_token = create_jwt(openid)
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": JWT_EXPIRE_MINUTES * 60,
        "scope": "openid profile email"
    }

# ========== OAuth 2.0 UserInfo 端点 ==========
@app.get("/oauth/userinfo")
async def userinfo(credentials: HTTPAuthorizationCredentials = Depends(security)):
    openid = verify_jwt(credentials.credentials)
    with get_openid_to_session() as db:
        session_id = db.get(openid, "")
    return {
        "id": openid,
        "username": openid,
        "displayName": f"微信用户_{openid[:8]}",
        "email": f"{openid}@wechat.local",
        "session_id": session_id
    }

# ========== 健康检查 ==========
@app.get("/health")
async def health():
    return {"status": "ok"}

if __name__ == "__main__":
    # 创建缓存目录
    os.makedirs(WXACODE_CACHE_DIR, exist_ok=True)
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)