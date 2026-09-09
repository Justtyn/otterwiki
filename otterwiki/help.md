## 用户指南（User Guide）

### 编辑与创建页面（Editing and creating pages）

你可以使用页面右上角的 <span class="btn btn-primary btn-sm btn-hlp"><i class="fas fa-pencil-alt"></i></span> 编辑现有页面。如果找不到该按钮，说明你没有编辑该页面的权限。不过你仍然可以通过 <span class="help-button"><span class="btn btn-square btn-sm"><i class="fas fa-ellipsis-v"></i></span> <i class="fas fa-caret-right"></i> <span class="btn btn-square btn-sm"><i class="fab fa-markdown"></i></span> 查看源代码</span> 查看页面的源代码。

要创建页面，请使用 <span class="help-button"><span class="btn btn-square btn-sm"><i class="far fa-file"></i></span> 创建页面</span> 按钮。接下来，你需要为要创建的页面取一个名称。为了帮助你组织页面结构，相关按钮提供了快捷方式，可以快速填入最近访问过的页面和目录的路径。**请注意：** 页面名称会被净化处理，`?$.#\` 以及末尾的斜杠 `/` 将被移除。关于如何在 [子目录](#subdirectories) 中创建页面，请参见下文。提交表单后，新页面会在编辑器中打开。如果该页面已经存在，则会打开现有页面。

你可以使用 <span class="btn btn-primary btn-sm btn-hlp"><i class="far fa-eye"></i></span> 预览你的更改。无论是编辑时还是在文章预览中，都可以通过 <span class="btn btn-success btn-sm btn-hlp"> <i class="fas fa-save"></i></span> 提交你的更改。此时会打开一个对话框，你可以在其中输入提交说明。要放弃更改，请使用 <span class="btn btn-danger btn-sm btn-hlp" style="border: None;" role="button"><i class="fas fa-window-close"></i></span> 返回页面视图。

#### 通用快捷键（General Shortcuts）

| 操作      | Linux、Windows、<br>MacOS |
|----------------|:-------------------:|
| 创建页面    | <kbd>c</kbd>        |
| 编辑页面      | <kbd>e</kbd>        |
| 切换侧边栏 | <kbd>[</kbd>        |
| 切换右侧边栏 | <kbd>]</kbd>        |
| 搜索         | <kbd>/</kbd>        |

#### 编辑器快捷键（Editor Shortcuts）


| 操作     | Linux、Windows                | MacOS                       |
|---------------|------------------------------|-----------------------------|
| 保存          | <kbd>Ctrl</kbd>-<kbd>S</kbd> | <kbd>Cmd</kbd>-<kbd>S</kbd> |
| 切换预览 | <kbd>Ctrl</kbd>-<kbd>P</kbd> | <kbd>Cmd</kbd>-<kbd>P</kbd> |
| 搜索        | <kbd>Ctrl</kbd>-<kbd>F</kbd> | <kbd>Cmd</kbd>-<kbd>F</kbd> |
| 查找下一个     | <kbd>Ctrl</kbd>-<kbd>G</kbd> | <kbd>Cmd</kbd>-<kbd>G</kbd> |
| 查找上一个 | <kbd>Shift</kbd>-<kbd>Ctrl</kbd>-<kbd>G</kbd> | <kbd>Shift</kbd>-<kbd>Cmd</kbd>-<kbd>G</kbd> |
| 替换       | <kbd>Shift</kbd>-<kbd>Ctrl</kbd>-<kbd>F</kbd> | <kbd>Cmd</kbd>-<kbd>Option</kbd>-<kbd>F</kbd> |
| 全部替换   | <kbd>Shift</kbd>-<kbd>Ctrl</kbd>-<kbd>R</kbd> | <kbd>Shift</kbd>-<kbd>Cmd</kbd>-<kbd>Option</kbd>-<kbd>F</kbd> |


为方便排版格式，可以使用以下快捷键：


|                     | Linux、Windows                | MacOS                       |
|---------------------|-------------------------------|-----------------------------|
| 粗体                | <kbd>Ctrl</kbd>-<kbd>B</kbd>  | <kbd>Cmd</kbd>-<kbd>B</kbd> |
| 斜体              | <kbd>Ctrl</kbd>-<kbd>I</kbd>  | <kbd>Cmd</kbd>-<kbd>I</kbd> |
| 删除线      | <kbd>Shift</kbd>-<kbd>Ctrl</kbd>-<kbd>S</kbd> | <kbd>Shift</kbd>-<kbd>Cmd</kbd>-<kbd>S</kbd> |
| 链接                | <kbd>Ctrl</kbd>-<kbd>K</kbd>  | <kbd>Cmd</kbd>-<kbd>K</kbd> |
| 插入/格式化表格 | <kbd>Ctrl</kbd>-<kbd>J</kbd>  | <kbd>Cmd</kbd>-<kbd>J</kbd> |


#### 页面历史（Page history）

你可以通过 <span class="help-button"><span class="btn btn-square btn-sm"><i class="fas fa-ellipsis-v"></i></span> <i class="fas fa-caret-right"></i> <span class="btn btn-square btn-sm"><i class="far fa-file-alt"></i></span> 历史</span> 查看页面的历史记录。该页面的所有编辑都会按顺序列出，并显示提交日期、作者和提交说明。

**比较修订版本：** 选择要比较的两个修订版本，然后点击 <span class="btn btn-primary btn-sm btn-hlp">比较修订版本</span>，即可显示差异。

**查看修订版本：** 你可以使用历史记录中的日期 <span class="help-button"><a href="#">YYYY-MM-DD hh:mm</a></span> 链接打开任意修订版本。

**查看单个提交：** 你可以使用修订链接查看单个提交，例如 <span class="help-button"><a href="#" class="btn revision-small">012abc</a></span>

**回退提交：** 你可以使用历史记录中的 <span class="help-button"><a href="#"><i class="fas fa-undo"></i></a></span> 链接回退某个提交。这会创建一个回退提交。

#### 页面追溯（Page blame）

通过 <span class="help-button"><span class="btn btn-square btn-sm"><i class="fas fa-ellipsis-v"></i></span> <i class="fas fa-caret-right"></i> <span class="btn btn-square btn-sm"><i class="fas fa-people-arrows"></i></span> 追溯</span>，你可以查看页面的源代码，其中每一行都标注了最后修改该行的修订版本信息以及提交作者。

**查看修订版本**：你可以使用该行的日期 <span class="help-button"><a href="#">YYYY-MM-DD HH:mm</a></span> 链接打开任意修订版本。

**查看单个提交**：你可以使用该行的修订链接查看某次特定提交时页面的状态，例如 <span class="help-button"><a href="#" class="btn revision-small">012abc</a></span>。

#### 页面重命名（Page rename）

你可以使用 <span class="help-button"><span class="btn btn-square btn-sm"><i class="fas fa-ellipsis-v"></i></span> <i class="fas fa-caret-right"></i> <span class="btn btn-square btn-sm"><i class="fas fa-edit"></i></span> 重命名</span> 重命名页面。重命名时适用与[创建页面](#editing-and-creating-pages)相同的规则。

附件会随重命名的页面一起移动。

#### 页面删除（Page delete）

页面（及其所有附件）可以通过 <span class="help-button"><span class="btn btn-square btn-sm"><i class="fas fa-ellipsis-v"></i></span> <i class="fas fa-caret-right"></i> <span class="btn btn-square btn-sm"><i class="far fa-trash-alt"></i></span> 删除</span> 删除。请注意：此删除操作可以恢复，An Otter Wiki 从不会让仓库遗忘任何内容。

#### 页面名称（Page name）

页面名称可以是任何能存储在文件系统中的名称，但会做一些净化处理：`?$.#\` 以及末尾的斜杠 `/` 会被移除。
由于所有页面都以全小写的文件名存储，页面名称的大小写由第一个标题决定。

注意：启用 `RETAIN_PAGE_NAME_CASE` 后，文件名的大小写将决定页面名称的大小写。

---

### 附件（Attachments）

为页面添加附件有两种方式。第一种，你可以通过 <span class="help-button"><span class="btn btn-square btn-sm"><i class="fas fa-ellipsis-v"></i></span> <i class="fas fa-caret-right"></i> <span class="btn btn-square btn-sm"><i class="fa fa-paperclip"></i></span> 附件</span> 访问当前页面的附件。第二种，在编辑页面时，你可以直接将图片粘贴到编辑器中。粘贴的图片会被上传并作为附件添加到你正在编辑的页面。

#### 编辑附件（Editing attachments）

通过 <span class="help-button"><span class="btn btn-square btn-sm"><i class="fas fa-ellipsis-v"></i></span> <i class="fas fa-caret-right"></i> <span class="btn btn-square btn-sm"><i class="fa fa-paperclip"></i></span> 附件</span> 打开附件菜单。除了上传之外，每个附件还可以通过 <span class="help-button"><a href="#"><i class="fas fa-edit"></i></a></span> 打开进行编辑，从而替换、重命名或删除附件。附件的历史记录也会显示出来，你可以通过 <span class="help-button"><a href="#"><i class="fas fa-undo"></i></a></span> 回退更改。

#### 内联附件图片（Inline attached images）

要在页面中内联显示图片，请使用 Markdown 语法：`![](/Page/attachment.jpg)`。

在较大的屏幕上，右侧会显示最近使用的附件列表。你可以从该列表中选择一个附件，并选择它的使用方式。然后使用复制图标（<span class="help-button"><a href="#" class="btn btn-xsm"><i class="fas fa-copy"></i></a></span>）将相应的 Markdown 代码插入编辑器。

##### 缩略图与图片缩放（Thumbnails and Image Resizing）

要生成附件图片的缩小版本，请在图片 URL 后附加 `?thumbnail`。例如：`![](/page/attachment.jpg?thumbnail)`。

默认情况下，缩略图的最大尺寸为 80x80 像素。你可以在 `?thumbnail` 选项后加上数字来自定义尺寸。例如，`?thumbnail=400` 会缩放图片，使其最长边不超过 400 像素，并保持宽高比。

**重要：** 缩略图*绝不会*被放大。

如需更精确地控制图片缩放，请使用 `?height=` 或 `?width=` 参数，分别用于指定期望的高度或宽度。除非同时指定 `?width=` 和 `?height=`，否则宽高比将保持不变。

---

### 搜索（Search）

搜索范围涵盖最近一次提交中所有页面的内容。结果按命中次数排序，匹配的页面名称会被优先展示。每个页面都会显示匹配部分的简要摘要。

<p>搜索默认不区分大小写。可以通过
<span class="help-button"><input type="checkbox" style="display:inline;" id="is_casesensitive" checked>
区分大小写 </span> 启用大小写敏感。</p>

<p>要进行更复杂的搜索，你可以使用正则表达式，通过
<span class="help-button"><input type="checkbox" style="display:inline;" id="is_regexp" checked>
正则表达式</span> 启用。要进行区分大小写的正则表达式搜索，请同时启用 <em>区分大小写</em> 和 <em>正则表达式</em>。</p>

---

### 页面索引（Page index）

页面索引提供所有页面的总览，你可以通过左侧边栏的 <span class="help-button"><span class="btn btn-square btn-sm"><i class="fas fa-list"></i></span> A-Z</span> 打开它。所有列出的页面按页面名称排序，并按首字母分组。

要列出所有页面的标题，请使用页面顶部的开关：
<div class="d-inline-block custom-switch font-size-12 btn-hlp" style="border-radius: 0.5rem; background-color: rgba(100, 100, 100, 0.1);">
  <input type="checkbox" id="switch-headings" value="">
  <label for="switch-headings">切换页面标题显示</label>
</div>

这可能会让页面索引显得比较杂乱。

---

### 变更记录（Changelog）

变更记录 <span class="help-button"><span class="btn btn-square btn-sm"><i class="fas fa-ellipsis-v"></i></span> <i class="fas fa-caret-right"></i> <span class="btn btn-square btn-sm"><i class="fas fa-history"></i></span> 变更记录</span> 显示在 wiki 中进行过的所有提交。对页面或其附件的每一处更改都会作为提交保存。

**查看修订版本**：你可以通过 **文件** 列中的链接，打开处于所列状态的每个页面。

**查看单个提交**：你可以使用所在行的修订链接查看某次特定提交时页面的状态，例如 <span class="help-button"><a href="#" class="btn revision-small">012abc</a></span>。

**回退提交：** 你可以使用历史记录中的 <span class="help-button"><a href="#"><i class="fas fa-undo"></i></a></span> 链接回退某个提交。这会创建一个回退提交。

---

### 子目录（Subdirectories）

在页面名称之前加上以斜杠分隔的子目录名称，即可在子目录中创建页面。例如：`Subdirectory/Page`。为了便于总览，每个子目录都有自己的页面索引。

子目录中还可以再建子目录。其上限取决于 git 和底层文件系统。在正常的人工使用情况下，几乎不可能触及这些上限。

---


[modeline]: # ( vim: set fenc=utf-8 spell spl=en sts=4 et tw=80: )
