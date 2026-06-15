Page({
  data: {
    // 无需 UI 状态，仅保留必要数据
  },

  onLoad(options) {
    // 1. 处理二维码参数（小程序码 scene 或普通二维码 q）
    let qrSessionId = '';

    if (options.scene) {
      qrSessionId = decodeURIComponent(options.scene);
      console.log('从小程序码获取到会话ID:', qrSessionId);
    } else if (options.q) {
      const qrUrl = decodeURIComponent(options.q);
      qrSessionId = this.getQueryParam(qrUrl, 'session_id');
      console.log('从普通二维码获取到会话ID:', qrSessionId);
    }

    // 2. 扫码进入：自动登录（无UI，直接执行）
    if (qrSessionId) {
      this.doLogin(qrSessionId);
    } else {
      // 非扫码进入：静默检查 token（仅控制台）
      const token = wx.getStorageSync('access_token');
      if (token) {
        console.log('已有登录凭证');
      }
    }
  },

  // 辅助：从 URL 字符串中提取指定参数的值
  getQueryParam(url, paramName) {
    const queryString = url.split('?')[1];
    if (!queryString) return '';
    const params = queryString.split('&');
    for (let i = 0; i < params.length; i++) {
      const [key, value] = params[i].split('=');
      if (key === paramName) {
        return decodeURIComponent(value);
      }
    }
    return '';
  },

  // 核心登录逻辑（与原有完全一致）
  async doLogin(sessionId) {
    if (!sessionId) {
      wx.showToast({ title: '无效的会话ID', icon: 'none' });
      return;
    }

    wx.showLoading({ title: '登录中...' });
    try {
      const loginRes = await wx.login();
      const code = loginRes.code;

      const res = await new Promise((resolve, reject) => {
        wx.request({
          url: 'https://xxx/api/bind_session',
          method: 'POST',
          data: { code, session_id: sessionId },
          header: { 'content-type': 'application/json' },
          success: resolve,
          fail: reject
        });
      });

      if (res.statusCode === 200 && res.data.access_token) {
        wx.setStorageSync('access_token', res.data.access_token);
        wx.showModal({
          title: '登录成功',
          content: '您已成功登录，可享受完整服务',
          showCancel: false
        });
      } else {
        throw new Error(res.data.detail || '登录失败');
      }
    } catch (err) {
      console.error(err);
      wx.showModal({
        title: '登录失败',
        content: err.message || '网络错误，请稍后重试',
        showCancel: false
      });
    } finally {
      wx.hideLoading();
    }
  },

  // 点击卡片：复制链接 + 弹窗提示（保留原有业务接口可选）
  copyLink(e) {
    const url = e.currentTarget.dataset.url;
    if (url) {
      wx.setClipboardData({
        data: url,
        success: () => {
          wx.showModal({
            title: '提示',
            content: '链接已复制，请在浏览器中打开',
            showCancel: false
          });
        }
      });
    } else {
      wx.showToast({ title: '链接无效', icon: 'none' });
    }
  },

  // 登录后自动调用业务接口
  async callBusinessApi() {
    const token = wx.getStorageSync('access_token');
    if (!token) return;
    try {
      const res = await new Promise((resolve, reject) => {
        wx.request({
          url: 'https://xxxx/api/userinfo',
          method: 'GET',
          header: { 'Authorization': `Bearer ${token}` },
          success: resolve,
          fail: reject
        });
      });
      if (res.statusCode === 200) {
        console.log('业务接口返回:', res.data);
      }
    } catch (err) {
      console.error('业务接口错误:', err);
    }
  }
});