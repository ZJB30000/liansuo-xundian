/* ============================================================
 * 报销智能助手 —— 查询页逻辑 query.js
 * ------------------------------------------------------------
 * 功能：员工输入"工号 + 姓名 + 验证码"核验身份后，
 *       仅查询本人单据的审核状态（严格"本人查本人"）。
 * ============================================================ */

/* 获取页面元素 */
var empIdInput = document.getElementById("empId");
var empNameInput = document.getElementById("empName");
var empPinInput = document.getElementById("empPin");
var queryBtn = document.getElementById("queryBtn");
var resultEl = document.getElementById("result");

/* ---------- 显示提示信息 ---------- */
function showMsg(type, text) {
  resultEl.innerHTML =
    '<div class="msg ' + type + ' show">' + text + '</div>';
}

/* ---------- 渲染单据表格 ---------- */
function renderBills(employee) {
  // 表单头部信息：显示查询者本人
  var headHtml =
    '<div class="msg info show">' +
    "当前查询人：" + employee.name + "（工号 " + employee.empId + "）" +
    " ｜ 以下仅显示本人的报销单据状态" +
    "</div>";

  // 无单据时显示空状态
  if (!employee.bills || employee.bills.length === 0) {
    resultEl.innerHTML = headHtml +
      '<div class="card"><div class="empty">暂无报销单据记录</div></div>';
    return;
  }

  // 组装表格行
  var rowsHtml = "";
  employee.bills.forEach(function (bill) {
    // 取状态对应的文字与样式
    var statusInfo = STATUS_MAP[bill.status] || { text: "未知", badgeClass: "badge waiting" };
    rowsHtml +=
      "<tr>" +
      "<td>" + bill.id + "</td>" +
      "<td>" + bill.type + "</td>" +
      "<td>￥" + bill.amount.toFixed(2) + "</td>" +
      "<td>" + bill.submitDate + "</td>" +
      "<td><span class='" + statusInfo.badgeClass + "'>" + statusInfo.text + "</span></td>" +
      "<td>" + bill.remark + "</td>" +
      "</tr>";
  });

  // 完整结果区域
  resultEl.innerHTML = headHtml +
    '<div class="card">' +
    '<table>' +
    "<thead><tr>" +
    "<th>单据编号</th><th>类型</th><th>金额</th><th>提交日期</th><th>状态</th><th>备注</th>" +
    "</tr></thead>" +
    "<tbody>" + rowsHtml + "</tbody>" +
    "</table>" +
    '<div class="form-tip" style="margin-top:10px;">数据来源：财务专员周敏核对后的模拟数据，仅作演示。</div>' +
    "</div>";
}

/* ---------- 点击查询按钮：核验身份并查询本人单据 ---------- */
function handleQuery() {
  var empId = empIdInput.value;
  var name = empNameInput.value;
  var pin = empPinInput.value;

  // 调用数据层核验函数，严格"本人查本人"
  var result = queryMyBills(empId, name, pin);

  if (!result.ok) {
    // 核验失败，显示错误信息
    showMsg("error", result.message);
    return;
  }

  // 核验通过，渲染本人单据
  renderBills(result.employee);
}

/* ---------- 绑定事件：按钮点击 / 回车触发查询 ---------- */
queryBtn.addEventListener("click", handleQuery);

/* 三个输入框按回车时都触发查询 */
[empIdInput, empNameInput, empPinInput].forEach(function (input) {
  input.addEventListener("keyup", function (event) {
    if (event.key === "Enter") {
      handleQuery();
    }
  });
});