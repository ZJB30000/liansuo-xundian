/* ============================================================
 * 报销智能助手 —— 数据层 data.js
 * ------------------------------------------------------------
 * 职责：
 *   1. 提供默认的【报销制度】数据（由行政专员"乔姐"维护）；
 *   2. 提供默认的【模拟单据】数据（由财务专员"周敏"核对录入）；
 *   3. 使用 localStorage 做持久化，双击 index.html 即可运行，
 *      不需要后端和数据库。
 *
 * 说明：
 *   - 制度数据存在 localStorage 的 KEY_RULES 中；
 *   - 员工与单据数据存在 localStorage 的 KEY_EMPLOYEES 中；
 *   - 首次打开时自动写入默认数据，管理页可"恢复默认数据"。
 * ============================================================ */

/* ---------- localStorage 的存储键名 ---------- */
var KEY_RULES = "baoxiao_rules";        // 制度数据
var KEY_EMPLOYEES = "baoxiao_employees"; // 员工与单据数据

/* ---------- 管理页登录密码（演示用，可自行修改） ---------- */
var ADMIN_PASSWORD = "123456";

/* ---------- 默认制度数据：由乔姐维护，全员可见 ---------- */
var DEFAULT_RULES = [
  {
    id: "R001",
    category: "差旅费",
    title: "差旅费报销标准",
    effectiveDate: "2025-03-01",
    status: "有效",
    content: "一、交通工具：\n" +
      "1. 火车：高铁/动车限二等座，凭票实报实销；\n" +
      "2. 飞机：航程 1000 公里以上可乘坐经济舱，超 2 小时飞行需提前申请；\n" +
      "3. 市内交通：地铁、公交按实际发生额报销。\n\n" +
      "二、住宿费：一线城市每晚不超过 400 元，其他城市每晚不超过 300 元，需提供住宿发票。\n\n" +
      "三、伙食补助：出差期间每人每天补助 80 元，不再单独报销餐费。"
  },
  {
    id: "R002",
    category: "办公用品",
    title: "办公用品采购报销规范",
    effectiveDate: "2025-03-01",
    status: "有效",
    content: "一、适用范围：办公文具、打印耗材、办公设备配件等。\n\n" +
      "二、前置要求：单笔金额超过 500 元须先提交采购申请单，经部门负责人审批后方可购买。\n\n" +
      "三、报销材料：发票 + 采购申请单审批截图，发票抬头须为公司全称。"
  },
  {
    id: "R003",
    category: "招待费",
    title: "业务招待费管理规定",
    effectiveDate: "2025-04-01",
    status: "有效",
    content: "一、接待标准：公司内部接待每人每餐不超过 100 元，外部客户接待每人每餐不超过 200 元。\n\n" +
      "二、报销要求：\n" +
      "1. 报销单须注明接待对象、人数、事由；\n" +
      "2. 附发票及消费明细清单。\n\n" +
      "三、接待需提前报备直属上级，未经报备的费用不予报销。"
  },
  {
    id: "R004",
    category: "通用",
    title: "发票开具与粘贴要求",
    effectiveDate: "2025-02-01",
    status: "有效",
    content: "一、发票要求：\n" +
      "1. 必须是正规发票，抬头为公司全称；\n" +
      "2. 报销单上须写明票据张数、金额；\n" +
      "3. 发票遗失须填写《票据遗失说明》，经财务审批后按 50% 报销。\n\n" +
      "二、粘贴要求：票据按类粘贴在报销单背面，电子发票打印后同样需要粘贴。"
  },
  {
    id: "R005",
    category: "流程",
    title: "报销流程与时限说明",
    effectiveDate: "2025-05-01",
    status: "有效",
    content: "一、流程：员工提交申请 → 部门负责人审批 → 财务专员核对（周敏）→ 财务复核 → 打款。\n\n" +
      "二、时限：\n" +
      "1. 每月 1 日 - 20 日为报销受理期，逾期顺延至下月；\n" +
      "2. 单据审核一般 5 个工作日内完成，特殊单据最长不超过 15 个工作日；\n" +
      "3. 打款在每月 25 日后统一进行。\n\n" +
      "三、发票日期须在报销当月往前 90 天内，超出时限不予受理。"
  }
];

/* ---------- 默认员工与模拟单据数据：由周敏核对录入 ----------
 * 每个员工含：
 *   empId  员工工号
 *   name   姓名
 *   pin    查询验证码（模拟"授权凭证"，演示用）
 *   bills  该员工的单据列表
 */
var DEFAULT_EMPLOYEES = [
  {
    empId: "E001",
    name: "张三",
    pin: "0826",
    bills: [
      { id: "BX20251001001", type: "差旅费", amount: 1260.5, submitDate: "2025-10-01", status: "passed", remark: "高铁二等座 + 3 晚住宿（杭州）" },
      { id: "BX20251005002", type: "办公用品", amount: 328.0, submitDate: "2025-10-05", status: "auditing", remark: "打印机硒鼓 2 个，发票已附" }
    ]
  },
  {
    empId: "E002",
    name: "李四",
    pin: "1357",
    bills: [
      { id: "BX20251002001", type: "招待费", amount: 460.0, submitDate: "2025-10-02", status: "paid", remark: "客户来访晚餐，已报备领导" },
      { id: "BX20250926002", type: "差旅费", amount: 890.0, submitDate: "2025-09-26", status: "paid", remark: "成都出差，机票与住宿" }
    ]
  },
  {
    empId: "E003",
    name: "王五",
    pin: "2468",
    bills: [
      { id: "BX20251003001", type: "办公用品", amount: 150.0, submitDate: "2025-10-03", status: "auditing", remark: "笔记本、签字笔一批" },
      { id: "BX20250920002", type: "差旅费", amount: 2100.0, submitDate: "2025-09-20", status: "rejected", remark: "机票为头等舱，超标准，需重新核算" },
      { id: "BX20251007003", type: "培训费", amount: 800.0, submitDate: "2025-10-07", status: "waiting", remark: "外部培训报名费，等待部门负责人审批" }
    ]
  },
  {
    empId: "E004",
    name: "赵六",
    pin: "9753",
    bills: [
      { id: "BX20250928001", type: "招待费", amount: 240.0, submitDate: "2025-09-28", status: "passed", remark: "部门团建晚餐" },
      { id: "BX20251004002", type: "办公用品", amount: 1200.0, submitDate: "2025-10-04", status: "waiting", remark: "办公椅 2 把，待补采购申请单" }
    ]
  }
];

/* ---------- 单据状态文字与样式映射 ---------- */
var STATUS_MAP = {
  waiting:  { text: "待审核", badgeClass: "badge waiting" },
  auditing: { text: "审核中", badgeClass: "badge auditing" },
  passed:   { text: "已通过", badgeClass: "badge passed" },
  paid:     { text: "已打款", badgeClass: "badge paid" },
  rejected: { text: "已驳回", badgeClass: "badge rejected" }
};

/* ============================================================
 * localStorage 工具函数
 * ============================================================ */

/* 读取并解析 localStorage 中的 JSON 数据，异常时返回 null */
function loadJSON(key) {
  try {
    var raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : null;
  } catch (e) {
    return null;
  }
}

/* 将数据序列化后写入 localStorage */
function saveJSON(key, data) {
  localStorage.setItem(key, JSON.stringify(data));
}

/* ---------- 制度数据读写 ---------- */

/* 获取制度列表：localStorage 没有数据时写入默认数据并返回 */
function getRules() {
  var rules = loadJSON(KEY_RULES);
  if (!rules) {
    rules = DEFAULT_RULES.slice();
    saveJSON(KEY_RULES, rules);
  }
  return rules;
}

/* 保存制度列表（乔姐在管理页保存时调用） */
function saveRules(rules) {
  saveJSON(KEY_RULES, rules);
}

/* 恢复默认制度数据 */
function restoreRules() {
  var rules = DEFAULT_RULES.slice();
  saveJSON(KEY_RULES, rules);
  return rules;
}

/* ---------- 员工与单据数据读写 ---------- */

/* 获取员工列表，首次自动写入模拟数据 */
function getEmployees() {
  var employees = loadJSON(KEY_EMPLOYEES);
  if (!employees) {
    employees = DEFAULT_EMPLOYEES.slice();
    saveJSON(KEY_EMPLOYEES, employees);
  }
  return employees;
}

/* 恢复默认员工与单据数据 */
function restoreEmployees() {
  var employees = DEFAULT_EMPLOYEES.slice();
  saveJSON(KEY_EMPLOYEES, employees);
  return employees;
}

/* ============================================================
 * 业务校验函数
 * ============================================================ */

/* 查询单据：严格"本人查本人"
 * 参数：
 *   empId  员工工号（必填）
 *   name   姓名（必填，须与工号匹配）
 *   pin    查询验证码（必填，须匹配）
 * 返回：
 *   { ok: true, employee: { empId, name, bills } } 查询成功
 *   { ok: false, message: "错误提示" }             查询失败
 */
function queryMyBills(empId, name, pin) {
  // 1. 参数非空校验
  if (!empId || !name || !pin) {
    return { ok: false, message: "工号、姓名和验证码都不能为空" };
  }

  // 2. 精确匹配：工号 + 姓名 + 验证码三者完全一致才放行
  var employees = getEmployees();
  var found = null;
  for (var i = 0; i < employees.length; i++) {
    var emp = employees[i];
    if (emp.empId === empId.trim().toUpperCase()) {
      found = emp;
      break;
    }
  }

  // 3. 工号不存在 → 拒绝
  if (!found) {
    return { ok: false, message: "未找到该工号，请核对后重试" };
  }

  // 4. 姓名不匹配 → 拒绝（防止只凭工号查询他人数据）
  if (found.name !== name.trim()) {
    return { ok: false, message: "姓名与工号不匹配，无法查询" };
  }

  // 5. 验证码不匹配 → 拒绝（模拟"授权凭证"）
  if (found.pin !== pin.trim()) {
    return { ok: false, message: "验证码错误，请向行政或财务确认授权验证码" };
  }

  // 6. 全部通过，只返回本人数据（不含验证码字段，避免泄露）
  return {
    ok: true,
    employee: {
      empId: found.empId,
      name: found.name,
      bills: found.bills
    }
  };
}