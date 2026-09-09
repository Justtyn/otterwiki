## 管理员指南（Admin Guide）

管理员用户可以对 An Otter Wiki 进行配置，他们可以在自己的 <span class="help-button"><span class="btn btn-square btn-sm"><i class="fas fa-ellipsis-v"></i></span> <i class="fas fa-caret-right"></i> <span class="btn btn-square btn-sm"><i class="fas fa-cog"></i></span> 设置</span> 侧边栏菜单中找到 <span class="help-button"><span class="btn btn-square btn-sm"><i class="fas fa-cogs"></i></span> 应用偏好设置</span>、<span class="help-button"><span class="btn btn-square btn-sm"><i class="fas fa-users"></i></span> 用户管理</span> 等条目。

### 品牌设置（Branding）

在 <span class="help-button"><span class="btn btn-square btn-sm"><i class="fas fa-cogs"></i></span> 应用偏好设置</span> 中，可以配置 <span class="help-button">站点名称</span>，它会显示在网站顶部的导航栏以及电子邮件中。

<span class="help-button">站点徽标</span> 显示在站点名称旁边，而 <span class="help-button">站点图标</span>（favicon）则显示在浏览器标签页和书签中。站点徽标和站点图标都可以使用附件。两者默认使用 An Otter Wiki 的徽标。

要隐藏 An Otter Wiki 的徽标，请勾选 <span class="help-button"><input type="checkbox" style="display:inline;" id="hide-logo" checked> 在侧边栏隐藏 An Otter Wiki 徽标</span>。此时会在 <span class="help-button"><span class="btn btn-square btn-sm"><i class="fas fa-ellipsis-v"></i></span></span> 菜单中添加一个指向"关于"信息的菜单项。

### 元数据（Meta data）

<span class="help-button">站点描述</span> 用于 `<meta name="description">` 标签。

<span class="help-button">站点语言</span> 用于配置在 wiki 页面上生成的 `<html>` 标签的 `lang` 属性。

<span class="help-button">机器人爬虫</span> 用于配置所生成的 `robots.txt`，设置为 `Disallow` 时，会告知来访的爬虫不得抓取站点内容。

### 用户管理（User management）

所有用户都列在 <span class="help-button"><span class="btn btn-square btn-sm"><i class="fas fa-users"></i></span> 用户管理</span> 下的表格中。你可以通过勾选和取消勾选复选框来更新用户的标志位：<span class="help-button"><input type="checkbox" style="display:inline;" id="true" checked></span> 表示已设置该标志，<span class="help-button"><input type="checkbox" style="display:inline;" id="false"></span> 表示未设置。设置了标志即向用户授予相应特权。

按用户授予的特权会叠加到全局权限之上。例如，如果全局设置只允许带有 **管理员** 标志的用户上传附件，那么可以让 `user@example.org` 获得上传权限而无需设置管理员标志。

**管理员** 列中带有 <span class="help-button"><input type="checkbox" style="display:inline;" id="true-admin" checked></span> 的用户拥有管理员权限。更改通过 <span class="btn btn-primary btn-sm btn-hlp">更新权限</span> 应用。

#### 编辑用户（Edit a user）

通过 <span class="help-button"><a hre="#"><i class="fas fa-user-edit"></i></a></span> 可以打开单个用户进行编辑。在这里你可以更新用户的 <span class="help-button">姓名</span> 和 <span class="help-button">电子邮箱</span>，并设置标志位和权限。修改用户的姓名或电子邮箱不会改变提交历史，只影响之后的提交。

与用户管理表格中一样，你可以使用复选框控制用户的标志位。

更改通过 <span class="btn btn-primary btn-sm btn-hlp">更新</span> 应用。

#### 删除用户（Delete a user）

在"编辑用户"页面上，你可以将用户从 wiki 的数据库中移除。勾选复选框并点击 <span class="btn btn-danger btn-sm btn-hlp" style="border: None;" role="button">删除</span>。注意：此操作不会更改任何编辑历史，也不会阻止该用户再次注册。

### 侧边栏偏好设置（Sidebar Preferences）

#### 快捷方式（Shortcuts）
常用的 wiki 功能，例如 <code>首页</code>、<code>页面索引</code>、<code>变更记录</code> 或 <code>创建页面</code>，都可以添加到侧边栏，例如 <span class="help-button"><span class="btn btn-square btn-sm"><i class="fas fa-list"></i></span> A - Z</span>。

#### 自定义菜单（Custom Menu）

可以配置自定义菜单，用于显示指向重要页面的链接或外部链接。对于 wiki 页面，只需输入页面名称；对于外部链接，请输入完整的 URL。此外，还可以为每个条目指定一个标题。菜单中条目的顺序可手动设置。

#### 页面索引（Page Index）

侧边栏可以配置为以不同模式显示页面索引。你可以按字母顺序显示页面和目录、目录优先，或只显示目录。你也可以选择完全不显示页面索引。

<code>页面索引聚焦</code> 设置控制哪些页面和文件夹与当前页面一同显示：

- <code>聚焦当前子树</code>（默认）：仅列出当前页面的父级和同级目录。其他文件夹会保持隐藏，直到你导航进入它们。
- <code>始终显示所有顶层文件夹和页面</code>：列出所有顶层文件夹和页面，但文件夹保持折叠，除非它们位于当前页面的路径上。
- <code>始终显示所有页面（全部展开）</code>：列出完整的页面树，并且所有文件夹都展开。

### 内容与编辑偏好设置（Content and Editing Preferences）

#### 提交说明（Commit Messages）
默认情况下，An Otter Wiki 要求用户在更新页面时填写提交说明。你可以通过 <span class="help-button">提交说明</span> 设置进行配置。将其设为 `optional` 将允许提交说明留空。

#### 页面名称大小写（Page case name）
An Otter Wiki 以全小写的文件名存储页面。要保留文件名的大小写，请勾选 <span class="help-button"><input type="checkbox" style="display:inline;" id="true-retain-page-name" checked>保留页面名称大小写</span>。

#### Git Web 服务器
通过 <span class="help-button"><input type="checkbox" style="display:inline;" id="true-git-webserver" checked> 启用 Git 服务器</span>，拥有读取权限的用户可以通过 git 克隆和拉取 wiki 内容，拥有上传/附件管理权限的用户可以推送内容。非匿名访问使用 HTTP Basic 认证。不支持通过 ssh 使用 git。启用后，用户可以在自己的设置中找到克隆仓库的 URL。


### 访问权限与注册偏好设置（Access Permissions and Registration Preferences）

用户要能够读取/写入页面或上传和修改附件，需要满足的条件由 <span class="help-button"><span class="btn btn-square btn-sm"><i class="fas fa-users-cog"></i></span> 权限与注册偏好设置</span> 控制。

- `读取权限` 允许用户查看页面和附件，包括历史记录和每一个单独的提交。
- `写入权限` 允许用户编辑页面。
- `附件权限` 允许用户上传和修改附件。

谁可以访问什么由以下选项定义：

- `匿名` - 任何人无需登录即可访问 wiki。
- `已注册` - 用户需要拥有账户并登录。
- `已批准` - 用户需要登录，且 <span class="help-button">已批准</span> 标志必须被设置。
- `管理员` - 用户需要登录，且 <span class="help-button">管理员</span> 标志必须被设置。

此外，你可以按用户配置特权。按用户授予的特权会叠加到全局权限之上。参见上文的 [用户管理](#user-management)。

通过 <span class="help-button"><input type="checkbox" style="display:inline;" id="true-reg-req" checked> 禁用注册</span>，你可以禁止任何人注册新账户。

配置 <span class="help-button"><input type="checkbox" style="display:inline;" id="true-reg-req" checked> 注册需要邮箱确认</span>，要求用户在账户启用之前确认其电子邮箱地址。这是为了防止用户使用输入错误的邮箱地址、甚至虚假的邮箱地址来注册。

如果用户需要经过批准，管理员需要手动设置该标志，或启用 <span class="help-button"><input type="checkbox" style="display:inline;" id="true-auto-approve" checked> 自动批准新注册用户</span>。当需要管理员批准用户时，<span class="help-button"><input type="checkbox" style="display:inline;" id="true-notify" checked> 新用户注册时通知管理员</span> 可以帮上忙。为了更加省心，可以启用 <span class="help-button"><input type="checkbox" style="display:inline;" id="true-notify" checked> 账户获得批准时通知用户</span>，这样用户会自动收到通知，无需你亲自通知。


### 邮件偏好设置（Mail Preferences）

要让 An Otter Wiki 能够向注册的用户发送邮件（例如重置丢失的密码）并就新用户通知管理员，请配置 <span class="help-button"><span class="btn btn-square btn-sm"><i class="fas fa-envelope"></i></span> 邮件偏好设置</span>。配置详情请参见 [flask-mail 文档](https://pythonhosted.org/Flask-Mail/)。

你可以使用 <span class="help-button">发送测试邮件</span> 来测试配置。默认情况下，测试邮件会发送给你自己。


[modeline]: # ( vim: set fenc=utf-8 spell spl=en sts=4 et tw=80: )
