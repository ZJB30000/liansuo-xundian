/* ============================================================
 * 报销智能助手 —— 管理页逻辑 admin.js
 * ------------------------------------------------------------
 * 功能：乔姐使用管理密码登录后，可对制度内容进行
 *       新增 / 编辑 / 删除 / 恢复默认 等维护操作。
 * 说明：登录态记录在 sessionStorage 中，关闭页面即失效。
 * ============================================================ */

/* 获取页面元素 */
var loginCard = document.getElementById("loginCard");
var adminArea = document.getElementById("adminArea");
var adminPassInput = document.getElementById("adminPass");
var loginBtn = document.getElementById("loginBtn");

var editIdInput = document.getElementById("editId");
var ruleCategoryInput = document.getElementById("ruleCategory");
var ruleTitleInput = document.getElementById("ruleTitle");
var ruleDateInput = document.getElementById("ruleDate");
var ruleContentInput = document.getElementById("ruleContent");
var formTitleEl = document.getElementById("formTitle");
var saveBtn = document.getElementById("saveBtn");
var cancelEditBtn = document.getElementById("cancelEditBtn");
var adminRuleListEl = document.getElementById("adminRuleList");

var restoreRulesBtn = document.getElementById("restoreRulesBtn");
var restoreAllBtn = document.getElementById("restoreAllBtn");
var logoutBtn = document.getElementById("logoutBtn");

/* sessionStorage 键名：记录管理登录态 */
var KEY_ADMIN_SESSION = "baoxiao_admin_logged";

/* ---------- 切换登录 / 管理界面 ---------- */
function showAdminArea() {
  loginCard.style.display = "none";
  adminArea.style.display = "block";
  renderAdminRules(); // 进入管理后刷新制度列表
}

function showLoginCard() {
  loginCard.style.display = "block";
  adminArea.style.display = "none";
}

/* ---------- 登录按钮 ---------- */
loginBtn.addEventListener("click", function () {
  // 校验管理密码（data.js 中的 ADMIN_PASSWORD）
  if (adminPassInput.value === ADMIN_PASSWORD) {
    sessionStorage.setItem(KEY_ADMIN_SESSION, "1");
    adminPassInput.value = "";
    showAdminArea();
  } else {
    alert("管理密码错误，请重试（演示密码：123456）");
  }
});

/* ---------- 退出管理 ---------- */
logoutBtn.addEventListener("click", function () {
  sessionStorage.removeItem(KEY_ADMIN_SESSION);
  showLoginCard();
  // 清空表单，避免遗留数据
  resetRuleForm();
});

/* ---------- 重置制度表单（用于新增 / 取消编辑） ---------- */
function resetRuleForm() {
  editIdInput.value = "";
  ruleTitleInput.value = "";
  ruleContentInput.value = "";
  ruleDateInput.value = "";
  ruleCategoryInput.value = "差旅费";
  formTitleEl.textContent = "新增制度";
  cancelEditBtn.style.display = "none";
}

/* ---------- 渲染管理页制度列表 ---------- */
function renderAdminRules() {
  var rules = getRules();
  adminRuleListEl.innerHTML = "";

  if (rules.length === 0) {
    adminRuleListEl.innerHTML = '<div class="empty">暂无制度，请先新增</div>';
    return;
  }

  rules.forEach(function (rule) {
    var item = document.createElement("div");
    item.className = "rule-item";
    item.innerHTML =
      '<div style="display:flex;justify-content:space-between;align-items:center;">' +
      "  <div>" +
      '    <span class="tag">' + rule.category + '</span>' +
      '    <span class="rule-title" style="font-size:14px;">' + rule.title + '</span>' +
      "  </div>" +
      '  <div class="row-actions">' +
      '    <button class="btn btn-secondary" onclick="editRule(\'' + rule.id + '\')">编辑</button>' +
      '    <button class="btn btn-danger" onclick="deleteRule(\'' + rule.id + '\')">删除</button>' +
      "  </div>" +
      "</div>" +
      '<div class="rule-meta">生效日期：' + rule.effectiveDate + ' ｜ 状态：' + rule.status + "</div>";

    adminRuleListEl.appendChild(item);
  });
}

/* ---------- 新增 / 保存制度 ---------- */
saveBtn.addEventListener("click", function () {
  // 表单校验：标题与正文不能为空
  var title = ruleTitleInput.value.trim();
  var content = ruleContentInput.value.trim();

  if (!title) {
    alert("请填写制度标题");
    return;
  }
  if (!content) {
    alert("请填写制度正文");
    return;
  }

  var rules = getRules();
  var editId = editIdInput.value;

  // 编辑模式：更新已有条目
  if (editId) {
    var target = null;
    for (var i = 0; i < rules.length; i++) {
      if (rules[i].id === editId) {
        target = rules[i];
        break;
      }
    }
    if (target) {
      target.category = ruleCategoryInput.value;
      target.title = title;
      target.effectiveDate = ruleDateInput.value || target.effectiveDate;
      target.content = content;
    }
  } else {
    // 新增模式：生成新 id 并追加
    var newRule = {
      id: "R" + String(Date.now()), // 以时间戳生成唯一 id
      category: ruleCategoryInput.value,
      title: title,
      effectiveDate: ruleDateInput.value || "2025-01-01",
      status: "有效",
      content: content
    };
    rules.unshift(newRule);
  }

  // 保存到 localStorage
  saveRules(rules);
  resetRuleForm();
  renderAdminRules();
  alert("保存成功，员工在主页即可看到最新制度。");
});

/* ---------- 编辑制度：将数据填入表单（按钮触发） ---------- */
function editRule(id) {
  var rules = getRules();
  var target = null;
  for (var i = 0; i < rules.length; i++) {
    if (rules[i].id === id) {
      target = rules[i];
      break;
    }
  }
  if (!target) {
    return;
  }

  // 回填表单并切换到"编辑模式"
  editIdInput.value = target.id;
  ruleCategoryInput.value = target.category;
  ruleTitleInput.value = target.title;
  ruleDateInput.value = target.effectiveDate;
  ruleContentInput.value = target.content;
  formTitleEl.textContent = "编辑制度";
  cancelEditBtn.style.display = "inline-block";
}

/* ---------- 删除制度（按钮触发） ---------- */
function deleteRule(id) {
  if (!confirm("确定删除该制度吗？删除后不可恢复。")) {
    return;
  }
  var rules = getRules();
  // 过滤掉目标条目
  var newRules = rules.filter(function (rule) {
    return rule.id !== id;
  });
  saveRules(newRules);
  renderAdminRules();
  alert("已删除，员工主页将不再显示该制度。");
}

/* ---------- 取消编辑 ---------- */
cancelEditBtn.addEventListener("click", resetRuleForm);

/* ---------- 恢复默认制度 ---------- */
restoreRulesBtn.addEventListener("click", function () {
  if (!confirm("将恢复为默认制度，当前修改会被覆盖，确定继续？")) {
    return;
  }
  restoreRules();
  renderAdminRules();
  alert("已恢复默认制度。");
});

/* ---------- 恢复全部默认数据 ---------- */
restoreAllBtn.addEventListener("click", function () {
  if (!confirm("将恢复全部默认数据（制度 + 单据模拟数据），确定继续？")) {
    return;
  }
  restoreRules();
  restoreEmployees();
  renderAdminRules();
  alert("已恢复全部默认数据。");
});

/* ---------- 页面初始化：根据登录态显示对应界面 ---------- */
if (sessionStorage.getItem(KEY_ADMIN_SESSION) === "1") {
  showAdminArea();
} else {
  showLoginCard();
}