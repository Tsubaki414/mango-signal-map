import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const root = dirname(scriptDir);
const shortlistPath = join(root, "head_ai_kol_shortlist.csv");
const databasePath = join(root, "kol_database", "deploy_seed.db");
const templatePath = join(root, "assets", "head_ai_kol_supplier_shortlist.template.html");
const outputPath = join(root, "head_ai_kol_and_supplier_shortlist.html");

function parseCsv(text) {
  const rows = [];
  let row = [];
  let field = "";
  let quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const char = text[i];
    if (quoted) {
      if (char === '"' && text[i + 1] === '"') {
        field += '"';
        i += 1;
      } else if (char === '"') {
        quoted = false;
      } else {
        field += char;
      }
    } else if (char === '"') {
      quoted = true;
    } else if (char === ",") {
      row.push(field);
      field = "";
    } else if (char === "\n") {
      row.push(field.replace(/\r$/, ""));
      rows.push(row);
      row = [];
      field = "";
    } else {
      field += char;
    }
  }
  if (field.length || row.length) {
    row.push(field.replace(/\r$/, ""));
    rows.push(row);
  }
  const headers = rows.shift().map((value) => value.replace(/^\uFEFF/, ""));
  return rows.filter((values) => values.some(Boolean)).map((values) =>
    Object.fromEntries(headers.map((header, index) => [header, values[index] ?? ""])),
  );
}

function query(sql) {
  const raw = execFileSync("sqlite3", ["-readonly", "-json", databasePath, sql], {
    encoding: "utf8",
  });
  return raw.trim() ? JSON.parse(raw) : [];
}

function clean(value) {
  return String(value ?? "")
    .replace(/[\u2013\u2014]/g, "-")
    .replace(/\s+/g, " ")
    .trim();
}

function profileUrl(account) {
  if (account.profile_url) return account.profile_url;
  const handle = String(account.handle || "").replace(/^@/, "");
  if (!handle) return "";
  const platform = String(account.platform || "").toLowerCase();
  if (platform === "x" || platform === "twitter") return `https://x.com/${handle}`;
  if (platform === "youtube") return `https://www.youtube.com/@${handle}`;
  if (platform === "instagram") return `https://www.instagram.com/${handle}/`;
  if (platform === "tiktok") return `https://www.tiktok.com/@${handle}`;
  return "";
}

function formatFollowers(value) {
  const count = Number(value || 0);
  if (!count) return "待核验";
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(count >= 10_000_000 ? 1 : 2).replace(/\.0+$/, "")}M`;
  if (count >= 1_000) return `${(count / 1_000).toFixed(count >= 100_000 ? 0 : 1).replace(/\.0$/, "")}K`;
  return String(count);
}

const shortlistSource = parseCsv(readFileSync(shortlistPath, "utf8"));
const kolRows = shortlistSource.map((row, index) => {
  const note = clean(row["备注"]);
  const priority = note.startsWith("S") ? "S" : "A";
  const followUp = clean(row["跟进时间"]);
  return {
    id: `kol-${index + 1}`,
    priority,
    platform: clean(row["平台"]),
    account: clean(row["账号"]),
    profileUrl: clean(row["主页链接"]),
    followers: clean(row["粉丝数"]) || "待核验",
    quote: clean(row["报价"]) || "待询价",
    contact: clean(row["联系方式"]) || "待补充",
    followUp: followUp || "未跟进",
    relationshipStatus: /已建联|已有回复|已有报价/.test(followUp + note) ? "已建联" : "待建联",
    evidence: note.replace(/^[SA][；;]\s*/, ""),
  };
});

const creators = query(`
  SELECT id, display_name, primary_handle, creator_class, internal_notes
  FROM creators
  ORDER BY id
`);
const accounts = query(`
  SELECT creator_id, platform, handle, profile_url, followers
  FROM social_accounts
  ORDER BY creator_id, id
`);
const contacts = query(`
  SELECT creator_id, method_type, value
  FROM contact_methods
  ORDER BY creator_id, id
`);

const accountsByCreator = new Map();
for (const account of accounts) {
  const list = accountsByCreator.get(account.creator_id) || [];
  list.push(account);
  accountsByCreator.set(account.creator_id, list);
}
const contactsByCreator = new Map();
for (const contact of contacts) {
  const list = contactsByCreator.get(contact.creator_id) || [];
  list.push(contact);
  contactsByCreator.set(contact.creator_id, list);
}

const shortlistHandles = new Set(
  kolRows.flatMap((row) => {
    const matches = `${row.account} ${row.profileUrl}`.match(/@([A-Za-z0-9_.-]+)/g) || [];
    return matches.map((match) => match.slice(1).toLowerCase());
  }),
);

const remainingCreators = creators.filter((creator) => {
  const handles = [creator.primary_handle, ...(accountsByCreator.get(creator.id) || []).map((item) => item.handle)]
    .filter(Boolean)
    .map((handle) => String(handle).replace(/^@/, "").toLowerCase());
  return !handles.some((handle) => shortlistHandles.has(handle));
});

function creatorBlob(creator) {
  return clean([
    creator.display_name,
    creator.primary_handle,
    creator.internal_notes,
    ...(contactsByCreator.get(creator.id) || []).map((item) => item.value),
  ].join(" | ")).toLowerCase();
}

function childRows(creatorsForSupplier, relationshipLabel) {
  return creatorsForSupplier.flatMap((creator) => {
    const creatorAccounts = accountsByCreator.get(creator.id) || [{
      platform: "待核验",
      handle: creator.primary_handle || creator.display_name,
      profile_url: "",
      followers: null,
    }];
    const creatorContacts = (contactsByCreator.get(creator.id) || [])
      .map((item) => clean(item.value))
      .filter(Boolean)
      .join("；") || "走供应商统一入口";
    return creatorAccounts.map((account) => ({
      creatorId: creator.id,
      creator: clean(creator.display_name),
      platform: clean(account.platform) || "待核验",
      handle: clean(account.handle) || clean(creator.primary_handle),
      profileUrl: profileUrl(account),
      followers: formatFollowers(account.followers),
      contact: creatorContacts,
      relationship: typeof relationshipLabel === "function" ? relationshipLabel(creator) : relationshipLabel,
    }));
  }).sort((a, b) => {
    const number = (value) => {
      if (/M$/i.test(value)) return parseFloat(value) * 1_000_000;
      if (/K$/i.test(value)) return parseFloat(value) * 1_000;
      return parseFloat(value) || 0;
    };
    return number(b.followers) - number(a.followers);
  });
}

const supplierDefinitions = [
  {
    id: "undercurrent",
    priority: "S",
    name: "UnderCurrent",
    type: "Talent management",
    contact: "cass@undercurrent.net",
    officialUrl: "https://undercurrent.net/",
    relationshipStatus: "经理已回复",
    verification: "高",
    nextAction: "索取 AI/tech roster、管理关系、价位、档期和 agency fee",
    match: (creator, blob) => creator.id === 247 || blob.includes("@undercurrent.net"),
    childRelationship: "经纪人邮箱与回复记录确认",
  },
  {
    id: "claryo",
    priority: "S",
    name: "Claryo Media",
    type: "AI campaign agency",
    contact: "hasan@claryomedia.com；WhatsApp +92 321 4675156",
    officialUrl: "https://claryomedia.com/",
    relationshipStatus: "机构待建联",
    verification: "中",
    nextAction: "索取 roster sample、creator 来源、最低预算、AI 案例和报告样例",
    match: (_creator, blob) => blob.includes("claryomedia.com"),
    childRelationship: "内部备注关联，代理授权待核验",
  },
  {
    id: "castle",
    priority: "S",
    name: "Castle & Castle",
    type: "Growth and creator agency",
    contact: "partnerships@castleandcastle.com",
    officialUrl: "https://castleandcastle.com/",
    relationshipStatus: "已有多个报价/回复",
    verification: "中高",
    nextAction: "统一索取完整 roster、独家状态、净价、批量折扣和账期",
    match: (_creator, blob) => blob.includes("castleandcastle.com") || blob.includes("castle and castle") || /\bc\s*&\s*c\b/.test(blob),
    childRelationship: (creator) => creatorBlob(creator).includes("castleandcastle.com") ? "机构域名邮箱确认" : "内部供应商标签",
  },
  {
    id: "passionfroot",
    priority: "S",
    name: "Passionfroot",
    type: "Creator booking platform",
    contact: "通过各 creator storefront Book Now",
    officialUrl: "https://www.passionfroot.me/creators",
    relationshipStatus: "可直接采购",
    verification: "高",
    nextAction: "有具体 brief 后筛 5-8 个 creator 发起首批 request",
    match: (_creator, blob) => blob.includes("passionfroot"),
    childRelationship: "公开 storefront，可预订但非独家代理",
  },
  {
    id: "talentube",
    priority: "A",
    name: "TalenTube",
    type: "Managed video network",
    contact: "collab@talentube.net；Telegram @TalenTube",
    officialUrl: "https://talentube.net/",
    relationshipStatus: "已有组合报价",
    verification: "中高",
    nextAction: "索取全部频道 URL、近 10 条表现、受众和频道控制权",
    match: (_creator, blob) => blob.includes("talentube"),
    childRelationship: "机构邮箱和多频道报价确认",
  },
  {
    id: "lmg",
    priority: "A",
    name: "Linus Media Group",
    type: "Owned media group",
    contact: "partnerships@linusmediagroup.com",
    officialUrl: "https://linusmediagroup.com/partners",
    relationshipStatus: "公开采购入口",
    verification: "高",
    nextAction: "仅在大型 AI infra、hardware 或 enterprise 预算明确时询价",
    match: (creator) => creator.display_name === "Linus Tech Tips",
    childRelationship: "集团自有频道",
  },
  {
    id: "portillo",
    priority: "A",
    name: "PortilloBoss Network",
    type: "Self-operated channel network",
    contact: "aiportilloboss@hotmail.com",
    officialUrl: "https://www.youtube.com/@AiPortillo",
    relationshipStatus: "已有报价",
    verification: "中",
    nextAction: "索取其声称管理的 8 个频道 URL 和 3 频道以上组合价",
    match: (creator) => creator.display_name.includes("PortilloBoss"),
    childRelationship: "本人报价称管理 8 个频道，完整清单待核验",
  },
  {
    id: "aiplanet",
    priority: "A",
    name: "AI Planet Network",
    type: "Self-operated channel network",
    contact: "sponsoraiplanet@gmail.com",
    officialUrl: "https://www.youtube.com/@AIEvolutione",
    relationshipStatus: "已有报价",
    verification: "中",
    nextAction: "核验 AI vs Terra 的主页和同一控制关系，再谈双频道 package",
    match: (creator) => creator.display_name.startsWith("AI Planet"),
    childRelationship: "同邮箱管理第二频道，主页待核验",
  },
  {
    id: "valid",
    priority: "A",
    name: "Valid",
    type: "AI-native ad agency",
    contact: "salim@valid.co；hello@valid.co",
    officialUrl: "https://www.valid.co/",
    relationshipStatus: "已有报价",
    verification: "高",
    nextAction: "仅用于 AI 虚拟网红、广告素材、whitelisting 和 media buying",
    match: (creator, blob) => creator.id === 297 || blob.includes("@valid.co"),
    childRelationship: "官网展示 Marcus，为虚拟网红而非真人 KOL",
  },
  {
    id: "neura",
    priority: "待核验",
    name: "Neura Agency 标签集群",
    type: "Unverified supplier cluster",
    contact: "neuraagency.partners@proton.me；Telegram Dineromentalidad",
    officialUrl: "",
    relationshipStatus: "暂缓采购",
    verification: "低",
    nextAction: "先核验法律主体、官网、账号授权和过往付款/交付证明",
    match: (_creator, blob) => blob.includes("neura agency"),
    childRelationship: "内部标签，同名公开公司不匹配，待核验",
  },
];

const supplierRows = supplierDefinitions.map((supplier) => {
  const matched = remainingCreators.filter((creator) => supplier.match(creator, creatorBlob(creator)));
  const linkedKols = childRows(matched, supplier.childRelationship);
  return {
    ...supplier,
    coverage: matched.length,
    linkedKols,
  };
});

const linkedCreatorIds = new Set(supplierRows.flatMap((supplier) => supplier.linkedKols.map((row) => row.creatorId)));
const data = {
  generatedAt: "2026-09-04",
  title: "Head AI KOL & Supplier Shortlist",
  sources: [
    "head_ai_kol_shortlist.csv",
    "kol_database/deploy_seed.db",
    "公开机构信息核验截至 2026-09-04",
  ],
  stats: {
    kolCount: kolRows.length,
    sCount: kolRows.filter((row) => row.priority === "S").length,
    aCount: kolRows.filter((row) => row.priority === "A").length,
    supplierCount: supplierRows.length,
    linkedCreatorCount: linkedCreatorIds.size,
  },
  kolRows,
  supplierRows,
};

const template = readFileSync(templatePath, "utf8");
const safeJson = JSON.stringify(data).replace(/</g, "\\u003c");
const html = template.replace("__DATA_JSON__", safeJson);
writeFileSync(outputPath, html, "utf8");
console.log(outputPath);
