# Google 登录发布配置

账号、阅读记录、收藏和私人书架不依赖 Google 凭据。Google 登录发布时需在
Google Cloud Console 创建同一项目下的 Web、Android、iOS 三个 OAuth Client。

- 服务端：配置 `OOHSTORY_GOOGLE_WEB_CLIENT_ID`、
  `OOHSTORY_GOOGLE_ANDROID_CLIENT_ID`、`OOHSTORY_GOOGLE_IOS_CLIENT_ID`。
- Android：在 OAuth Client 中登记正式包名和发布证书 SHA-1/SHA-256；构建时传入
  `--dart-define=GOOGLE_WEB_CLIENT_ID=<web-client-id>`。
- iOS：在 OAuth Client 中登记 Bundle ID；构建时同时传入
  `--dart-define=GOOGLE_WEB_CLIENT_ID=<web-client-id>` 和
  `--dart-define=GOOGLE_IOS_CLIENT_ID=<ios-client-id>`，并把 iOS Client ID 的反向
  URL Scheme 写入 `ios/Runner/Info.plist`。

当前实现使用 Google Identity Services ID Token，并在服务端按 Client ID、签名、
issuer 和 audience 校验，不走授权码回调，因此不需要 Web Client Secret。OAuth
Client ID 是公开标识，可以写入服务器配置和客户端；Client Secret 禁止提交到
Git、Flutter 包或网页静态文件，也不应通过群聊传递。

Google 只能绑定已经通过邀请码创建的 OOH STORY 账户，且 Google 验证邮箱必须与
注册邮箱完全一致。用户先用邮箱密码登录，再在个人中心主动绑定；绑定前 Google
不能创建新用户，也不能按同邮箱静默接管账户。绑定完成后，同一个 Google 身份可在
Web、Android、iOS 三端直接登录。

邮箱验证需要服务端配置 SMTP：`OOHSTORY_SMTP_HOST`、`OOHSTORY_SMTP_PORT`、
`OOHSTORY_SMTP_USERNAME`、`OOHSTORY_SMTP_FROM` 和仅服务用户可读的
`OOHSTORY_SMTP_PASSWORD_FILE`。SMTP 未配置时系统不会伪称邮件已发送，上传权限
保持关闭；完成 Google 验证的账户不受此限制。
