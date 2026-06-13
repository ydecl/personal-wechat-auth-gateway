Page({
  data: {
    sessionId: '',        // 用户输入的会话ID
    hasToken: false,      // 是否已有 token
    result: '',           // 显示接口返回结果
    loginStatus: ''       // 登录状态提示
  },

  onLoad(options) {
    // 1. 首先处理从二维码（小程序码或普通二维码）传入的参数
    let qrSessionId = '';

    // 判断是否通过小程序码进入（参数在 options.scene 中）
    if (options.scene) {
      // scene 是经过 encodeURIComponent 编码的，需要解码一次
      qrSessionId = decodeURIComponent(options.scene);
      console.log('从小程序码获取到会话ID:', qrSessionId);
    }
    // 判断是否通过普通链接二维码进入（参数在 options.q 中）
    else if (options.q) {
      const qrUrl = decodeURIComponent(options.q);
      qrSessionId = this.getQueryParam(qrUrl, 'session_id');
      console.log('从普通二维码获取到会话ID:', qrSessionId);
    }

    // 2. 如果从二维码中成功获取到会话ID，则自动填充并登录
    if (qrSessionId) {
      // 自动填充到输入框
      this.setData({ 
        sessionId: qrSessionId,
        loginStatus: `✅ 已自动获取会话ID: ${qrSessionId}`
      });
      // 自动执行登录（无需用户点击）
      this.onLogin();
    } 
    // 3. 非扫码进入时，检查本地是否有 token
    else {
      const token = wx.getStorageSync('access_token');
      if (token) {
        this.setData({ hasToken: true });
        this.showResult('已有登录凭证，可直接调用业务接口');
      }
    }
  },

  // 辅助函数：从 URL 字符串中提取指定参数的值
  getQueryParam(url, paramName) {
    // 分离出 "?" 后面的查询字符串
    const queryString = url.split('?')[1];
    if (!queryString) return '';
    // 按 "&" 拆分每个参数
    const params = queryString.split('&');
    for (let i = 0; i < params.length; i++) {
      const [key, value] = params[i].split('=');
      if (key === paramName) {
        return decodeURIComponent(value);
      }
    }
    return '';
  },

  // 用户手动输入会话ID时触发
  onSessionIdInput(e) {
    this.setData({ sessionId: e.detail.value });
  },

  // 登录逻辑（保持不变，会自动使用 data.sessionId）
  async onLogin() {
    const sessionId = this.data.sessionId.trim();
    if (!sessionId) {
      wx.showToast({ title: '请输入会话ID', icon: 'none' });
      this.setData({ loginStatus: '❌ 请输入会话ID' });
      return;
    }

    wx.showLoading({ title: '登录中...' });
    this.setData({ loginStatus: '⏳ 正在登录...' });

    try {
      const loginRes = await wx.login();
      const code = loginRes.code;

      const res = await new Promise((resolve, reject) => {
        wx.request({
          url: 'https://域名/api/bind_session',
          method: 'POST',
          data: { code, session_id: sessionId },
          header: { 'content-type': 'application/json' },
          success: resolve,
          fail: reject
        });
      });

      if (res.statusCode === 200 && res.data.access_token) {
        wx.setStorageSync('access_token', res.data.access_token);
        this.setData({ hasToken: true });
        wx.showToast({ title: '登录成功', icon: 'success' });
        this.showResult('✅ 登录成功！access_token 已保存');
      } else {
        throw new Error(res.data.detail || '登录失败');
      }
    } catch (err) {
      console.error(err);
      wx.showToast({ title: err.message || '登录失败', icon: 'none' });
      this.showResult(`❌ 登录失败：${err.message || '未知错误'}`);
    } finally {
      wx.hideLoading();
    }
  },

  async callBusinessApi() {
    const token = wx.getStorageSync('access_token');
    if (!token) {
      wx.showToast({ title: '请先登录', icon: 'none' });
      this.setData({ loginStatus: '⚠️ 请先登录' });
      return;
    }

    wx.showLoading({ title: '请求中...' });
    this.setData({ loginStatus: '⏳ 请求业务接口...' });

    try {
      const res = await new Promise((resolve, reject) => {
        wx.request({
          url: 'https://域名/api/userinfo',
          method: 'GET',
          header: {
            'Authorization': `Bearer ${token}`,
            'content-type': 'application/json'
          },
          success: resolve,
          fail: reject
        });
      });

      if (res.statusCode === 200) {
        this.showResult(`✅ 业务接口返回：${JSON.stringify(res.data, null, 2)}`);
      } else {
        throw new Error(res.data.detail || '请求失败');
      }
    } catch (err) {
      console.error(err);
      wx.showToast({ title: err.message, icon: 'none' });
      this.showResult(`❌ 业务接口错误：${err.message}`);
    } finally {
      wx.hideLoading();
    }
  },

  showResult(msg) {
    this.setData({ result: msg, loginStatus: msg });
  }
});