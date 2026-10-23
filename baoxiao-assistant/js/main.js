/* ============================================================
 * 报销智能助手 —— 主页逻辑 main.js
 * ------------------------------------------------------------
 * 功能：渲染制度列表、关键词搜索、重置
 * ============================================================ */

/* 获取页面元素 */
var searchInput = document.getElementById("searchInput");
var searchBtn = document.getElementById("searchBtn");
var resetBtn = document.getElementById("resetBtn");
var ruleListEl = document.getElementById("ruleList");
var emptyTipEl = document.getElementById("emptyTip");

/* 记录当前搜索关键词（用于在输入内容变化时实时筛选） */
var currentKeyword = "";

/* ---------- 渲染制度列表 ---------- */
function renderRules(rules) {
  // 清空容器
  ruleListEl.innerHTML = "";

  // 空结果显示提示
  if (!rules || rules.length === 0) {
    emptyTipEl.style.display = "block";
    return;
  }
  emptyTipEl.style.display = "none";

  // 逐条渲染制度卡片
  rules.forEach(function (rule) {
    var item = document.createElement("div");
    item.className = "rule-item";

    // 标题 + 元信息（分类标签 / 生效日期 / 状态）
    var metaHtml =
      '<span class="tag">' + rule.category + '</span>' +
      "生效日期：" + rule.effectiveDate +
      " ｜ 状态：" + rule.status;

    item.innerHTML =
      '<div style="display:flex;justify-content:space-between;align-items:flex-start;">' +
      '  <div class="rule-title">' + rule.title + '</div>' +
      '  <button class="btn btn-secondary" onclick="toggleContent(\'' + rule.id + '\')" style="padding:4px 10px;font-size:12px;" id="btn_' + rule.id + '">展开</button>' +
      '</div>' +
      '<div class="rule-meta">' + metaHtml + '</div>' +
      '<div class="rule-content rule-content-hidden" id="content_' + rule.id + '">' + rule.content + '</div>';

    ruleListEl.appendChild(item);
  });
}

/* ---------- 展开 / 收起制度详情 ---------- */
function toggleContent(id) {
  var contentEl = document.getElementById("content_" + id);
  var btnEl = document.getElementById("btn_" + id);

  // 切换显示/隐藏
  var isHidden = contentEl.classList.contains("rule-content-hidden");
  contentEl.classList.toggle("rule-content-hidden");
  btnEl.textContent = isHidden ? "收起" : "展开";
}

/* ---------- 关键词过滤 ---------- */
function filterRules(keyword) {
  var rules = getRules();
  // 保留大小写不敏感、去空格
  var kw = keyword.trim().toLowerCase();

  if (!kw) {
    return rules;
  }

  // 在标题、分类、正文中搜索关键词
  return rules.filter(function (rule) {
    return (
      rule.title.toLowerCase().indexOf(kw) !== -1 ||
      rule.category.toLowerCase().indexOf(kw) !== -1 ||
      rule.content.toLowerCase().indexOf(kw) !== -1
    );
  });
}

/* ---------- 执行一次搜索（按钮点击 / 回车触发） ---------- */
function doSearch() {
  currentKeyword = searchInput.value;
  renderRules(filterRules(currentKeyword));
}

/* ---------- 重置搜索 ---------- */
function doReset() {
  currentKeyword = "";
  searchInput.value = "";
  renderRules(getRules());
}

/* ---------- 绑定事件 ---------- */
searchBtn.addEventListener("click", doSearch);
resetBtn.addEventListener("click", doReset);
searchInput.addEventListener("keyup", function (event) {
  // 回车触发搜索
  if (event.key === "Enter") {
    doSearch();
  }
});

/* ---------- 页面初始化：展示全部制度 ---------- */
renderRules(getRules());