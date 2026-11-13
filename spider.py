# -*- coding: utf-8 -*-
"""
爬虫机器人 spider.py（第二版：双数据源 + 已结束岗位识别）
==========================================================
工作流程：
1. 访问两个公开数据源（GitHub 开源项目，每日更新）：
   主源 xixicc186/xixicc2027「2027届秋招信息汇总」
   扩源 HU-eun/campus-recruitment「2027届校招信息汇总平台」（约 1 万条，含 9 月以来历史岗位）
2. 筛选应届可投岗位（2027 届 + 届别不限），整理成网页需要的格式
3. 已过截止日期的岗位打上 ended 标记（页面会置灰、排到最后、归入"已结束"分类）
4. 备份旧的 data.js，然后写入新数据

合规三原则：只读公开数据 / 低频访问（每天1次）/ 展示时注明来源
"""
import json
import datetime
import os
import re
import subprocess
try:
    import requests
except ImportError:          # 没装 requests 就用标准库，保证爬虫永远能跑
    requests = None
import urllib.request

# ===== 数据源（主源 + 备用镜像，一个挂了自动换下一个）=====
SOURCES_XIXICC = [
    "https://raw.githubusercontent.com/xixicc186/xixicc2027/main/jobs.json",
    "https://cdn.jsdelivr.net/gh/xixicc186/xixicc2027@main/jobs.json",
]
SOURCES_HUEUN = [
    "https://raw.githubusercontent.com/HU-eun/campus-recruitment/main/data/jobs.json",
    "https://cdn.jsdelivr.net/gh/HU-eun/campus-recruitment@main/data/jobs.json",
]

# 只补充 9 月以来的已结束岗位（再往前的太旧，塞进来只会占地方）
KEEP_ENDED_SINCE = datetime.date(2026, 9, 1)

# spider.py 和 data.js 放在同一个文件夹里
HERE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(HERE, "data.js")


def fetch_json(urls, timeout=60, cache_name=None):
    """访问数据源，哪个能用就用哪个。
    大文件（扩源 9MB）用 Python 流下载容易断，自动降级到 curl；
    成功后缓存到本地，源全挂时用昨天的缓存兜底。"""
    cache_file = os.path.join(HERE, cache_name) if cache_name else None
    last_err = None
    for url in urls:
        data = None
        # 第一步：Python 直接下
        try:
            print("正在访问数据源:", url)
            if requests is not None:
                r = requests.get(url, timeout=timeout,
                                 headers={"User-Agent": "job-site-spider/1.0"})
                r.raise_for_status()
                data = r.json()
            else:
                req = urllib.request.Request(url, headers={"User-Agent": "job-site-spider/1.0"})
                data = json.load(urllib.request.urlopen(req, timeout=timeout))
        except Exception as e:
            print("  Python 下载失败:", str(e)[:80])
        # 第二步：curl 兜底（断流重传更稳）
        if data is None:
            try:
                print("  改用 curl 下载…")
                p = subprocess.run(
                    ["curl", "-sL", "--retry", "3", "--max-time", str(timeout * 2), url],
                    capture_output=True, timeout=timeout * 2 + 30)
                if p.returncode == 0 and p.stdout:
                    data = json.loads(p.stdout.decode("utf-8"))
            except Exception as e:
                print("  curl 也失败:", str(e)[:80])
        if data is not None:
            print("  访问成功！共 %d 条" % (len(data) if hasattr(data, "__len__") else -1))
            if cache_file:
                try:
                    with open(cache_file, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False)
                except Exception:
                    pass
            return data
    # 第三步：全部失败 → 用本地缓存（昨天的数据总比没有强）
    if cache_file and os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            print("  所有源都失败，使用本地缓存 %s（%d 条）" % (cache_name, len(data)))
            return data
        except Exception:
            pass
    return None


OVERSEAS_WORDS = ("刚果", "津巴布韦", "几内亚", "海外", "印度尼西亚", "老挝", "秘鲁", "阿根廷", "赞比亚")
PROVINCE_PREFIX = ("浙江", "江苏", "广东", "四川", "湖南", "湖北", "河北", "河南", "安徽", "福建",
                   "山东", "山西", "陕西", "江西", "贵州", "云南", "广西", "甘肃", "青海", "海南",
                   "辽宁", "吉林", "黑龙江", "内蒙古", "新疆", "西藏", "宁夏")


def clean_city(locations):
    """把数据里的地点整理成单个城市名，比如 ['北京','杭州'] 取北京"""
    if not locations:
        return "未公布"          # 源数据没写地点，如实标注
    parts = str(locations[0]).replace("，", "、").replace(",", "、").split("、") if isinstance(locations, list) else \
        str(locations).replace("，", "、").replace(",", "、").split("、")
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if p in ("全国", "全国各地", "多地", "全国多地", "大湾区各分行"):
            return "全国"
        for w in OVERSEAS_WORDS:
            if w in p:
                return "海外"
        if "省" in p:
            p = p.split("省")[-1].strip()
        if "市" in p:
            p = p.split("市")[0].strip()
        for prov in PROVINCE_PREFIX:
            if p.startswith(prov) and len(p) > len(prov):
                p = p[len(prov):]
                break
        p = p.rstrip("市")
        return p or "未公布"
    return "未公布"


def is_new(updated, today):
    """7 天内更新的岗位，标记 NEW"""
    d = parse_date(updated)
    if d:
        return (today - d).days <= 7
    return False


DATE_RE = re.compile(r"(\d{4})\s*[./\-年]\s*(\d{1,2})\s*[./\-月]\s*(\d{1,2})")


def parse_date(s):
    """从各种格式里抠出日期：2026/10/31、2026.10.31、2026-10-31、2026年10月31日"""
    m = DATE_RE.search(str(s or ""))
    if not m:
        return None
    try:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def is_http(u):
    u = (u or "").strip().lower()
    return u.startswith("http://") or u.startswith("https://")


def is_weixin(u):
    return "weixin.qq.com" in (u or "").lower()


def usable_notice_url(u):
    """能当「查看招聘公告」用的链接：http 且不是微信短链。
    微信短链（weixin.qq.com/r/...）在电脑浏览器点开会弹'下载微信'，体验极差；
    mp.weixin.qq.com 的公众号文章页浏览器可正常阅读，保留。"""
    u = (u or "").strip()
    if not is_http(u):
        return False
    low = u.lower()
    # 微信短链域名（mp.weixin.qq.com 文章页除外）
    if "weixin.qq.com" in low and "mp.weixin.qq.com" not in low:
        return False
    # 其他会跳 APP/异常的短链
    for bad in ("xhslink.cn/", "lxi.me/", "jsj.top/"):
        if bad in low:
            return False
    return True


def normalize_deadline(s):
    """截止时间统一成年-月-日；'招满为止'这类滚动招聘保留原文"""
    s = str(s or "").strip()
    d = parse_date(s)
    if d:
        return d.isoformat()
    return s or "未公布"


def split_positions(s):
    """'质量管理员，质量技术员，设备技术员' → ['质量管理员','质量技术员','设备技术员']"""
    parts = re.split(r"[，,、;；\n]+", str(s or ""))
    out = []
    for p in parts:
        p = p.strip()
        if p and len(out) < 8:                # 最多记 8 个方向，控制文件体积
            out.append(p[:40])
    return out


# ============================================================
# 主源 xixicc186/xixicc2027 的字段转换
# ============================================================
def official_link(item):
    """只认真正的官网投递链接。微信链接、二维码一律不算官网渠道。"""
    u = (item.get("apply_url") or "").strip()
    if not u or is_weixin(u) or not is_http(u):
        return ""
    return u


def delivery_channel(item):
    if official_link(item):
        return "官网"
    if (item.get("official_wechat") or "").strip() or (item.get("qr_image") or "").strip():
        return "公众号"
    text = " ".join(str(item.get(k) or "") for k in ("program", "positions", "batch"))
    if "@" in text:
        return "邮箱"
    return "其他"


# ============================================================
# 知名企业官方招聘网站对照表（人工核实，只收确定的真实官网）
# 原则：数据源没给链接、但公司官网招聘入口是公开常识的，
#       补挂官网直链；拿不准的公司一律不加，宁可走搜索兜底。
# 匹配规则：公司名（转小写）包含关键词即命中，按顺序取第一个。
# ============================================================
OFFICIAL_SITES = [
    # 互联网大厂
    ("腾讯", "https://join.qq.com"),
    ("字节跳动", "https://jobs.bytedance.com"),
    ("阿里巴巴", "https://campus.alibaba.com"),
    ("阿里云", "https://campus.alibaba.com"),
    ("蚂蚁", "https://talent.antgroup.com"),
    ("网易", "https://campus.163.com"),
    ("京东", "https://campus.jd.com"),
    ("百度", "https://talent.baidu.com"),
    ("美团", "https://campus.meituan.com"),
    ("拼多多", "https://careers.pinduoduo.com"),
    ("哔哩哔哩", "https://jobs.bilibili.com"),
    ("bilibili", "https://jobs.bilibili.com"),
    ("快手", "https://campus.kuaishou.com"),
    ("小红书", "https://job.xiaohongshu.com"),
    ("滴滴", "https://campus.didiglobal.com"),
    ("携程", "https://campus.ctrip.com"),
    ("同程", "https://campus.ly.com"),
    # 硬件 / 智能终端 / 通信
    ("华为", "https://career.huawei.com"),
    ("荣耀", "https://career.honor.com"),
    ("小米", "https://hr.xiaomi.com/campus"),
    ("oppo", "https://careers.oppo.com"),
    ("vivo", "https://hr.vivo.com/campus"),
    ("中兴", "https://job.zte.com.cn"),
    ("大疆", "https://we.dji.com"),
    ("海康威视", "https://campus.hikvision.com"),
    ("大华股份", "https://career.dahuatech.com"),
    ("tp-link", "https://hr.tp-link.com.cn/campus"),
    ("深信服", "https://career.sangfor.com"),
    ("科大讯飞", "https://campus.iflytek.com"),
    ("京东方", "https://job.boe.com"),
    ("传音", "https://careers.transsion.com"),
    ("歌尔", "https://zhaopin.goertek.com"),
    ("立讯", "https://career.luxshare-ict.com"),
    # 汽车 / 新能源
    ("比亚迪", "https://job.byd.com"),
    ("宁德时代", "https://career.catl.com"),
    ("蔚来", "https://campus.nio.cn"),
    ("小鹏汽车", "https://job.xiaopeng.com"),
    ("阳光电源", "https://career.sungrowpower.com"),
    ("汇川", "https://zhaopin.inovance.com"),
    ("亿纬", "https://zhaopin.evepower.com"),
    ("欣旺达", "https://career.sunwoda.com"),
    # 家电 / 制造 / 软件
    ("美的", "https://campus.midea.com"),
    ("海尔", "https://career.haier.com"),
    ("海信", "https://jobs.hisense.com"),
    ("格力", "https://zhaopin.gree.com"),
    ("tcl", "https://career.tcl.com"),
    ("浪潮集团", "https://zhaopin.inspur.com"),
    ("用友", "https://career.yonyou.com"),
    ("金蝶", "https://zhaopin.kingdee.com"),
    ("顺丰", "https://campus.sf-express.com"),
    # 游戏
    ("米哈游", "https://job.mihoyo.com"),
    ("完美世界", "https://job.wanmei.com"),
    ("三七互娱", "https://zhaopin.37.com"),
    # 央国企 / 金融
    ("国家电网", "https://zhaopin.sgcc.com.cn"),
    ("中国移动", "https://job.10086.cn"),
    ("中国联通", "https://job.10010.com.cn"),
    ("中国电信", "https://job.10000.com.cn"),
    ("招商银行", "https://career.cmbchina.com"),
    ("工商银行", "https://job.icbc.com.cn"),
    ("建设银行", "https://job.ccb.com"),
    ("农业银行", "https://job.abchina.com"),
    ("交通银行", "https://job.bankcomm.com"),
    ("中国银行", "https://campus.boc.cn"),
    ("平安", "https://careers.pingan.com"),
    # 教育 / 其他
    ("猿辅导", "https://career.yuanfudao.com"),
    ("猿力", "https://career.yuanfudao.com"),
    ("作业帮", "https://job.zuoyebang.com"),
    ("新东方", "https://zhaopin.xdf.cn"),
    ("好未来", "https://job.tal.com.cn"),
    ("shein", "https://www.sheincorp.cn"),
    ("同花顺", "https://job.10jqka.com.cn"),
]


# 误挂排除规则：关键词容易撞上不相关公司名，命中排除词就跳过该关键词
# 例如「北京东西分析仪器」含"京东"、「星际荣耀」是火箭公司不是荣耀手机
EXCLUDE_RULES = {
    "京东": ("京东方", "东西"),
    "荣耀": ("星际荣耀",),
    "蚂蚁": ("社会工作",),
    "海信": ("上海信息",),
}


def enrich_official_sites(jobs):
    """给没有官网链接、但公司命中对照表的岗位补挂官网直链。"""
    added = 0
    for j in jobs:
        if j.get("official_url"):
            continue
        co = (j.get("company") or "").lower()
        if not co:
            continue
        for kw, url in OFFICIAL_SITES:
            if kw not in co:
                continue
            if any(x in co for x in EXCLUDE_RULES.get(kw, ())):
                continue  # 撞名公司，换下一个关键词再试
            j["official_url"] = url
            j["delivery_channel"] = "官网"
            added += 1
            break
    return added


def convert_xixicc(raw_jobs, today):
    result = []
    for item in raw_jobs:
        cohort = str(item.get("cohort") or "")
        if "2026" in cohort and "2027" not in cohort:
            continue
        if cohort and "2027" not in cohort and "不限" not in cohort:
            continue

        positions = item.get("positions") or []
        if positions:
            title = "、".join(str(p) for p in positions[:3])
        else:
            ind = item.get("industry") or "综合"
            if ind == "其他":
                ind = "综合"
            title = item.get("program") or (ind + "类岗位")

        deadline = item.get("deadline") or "未公布"
        d = parse_date(deadline)
        ended = bool(d and d < today)
        updated = item.get("last_seen") or item.get("first_seen") or ""
        of_url = official_link(item)
        channel = delivery_channel(item)
        result.append({
            "company": item.get("company") or "未知公司",
            "title": "%s %s" % (cohort, title),
            "positions": [str(p) for p in positions[:8]],
            "program": item.get("program") or "",
            "city": clean_city(item.get("locations")),
            "type": item.get("industry") or "其他",
            "batch": item.get("batch") or "",
            "deadline": normalize_deadline(deadline),
            "updated": updated,
            "isNew": is_new(updated, today),
            "ended": ended,
            "official_url": of_url,
            "info_url": (item.get("official_wechat") or "").strip()
                        if usable_notice_url(item.get("official_wechat")) else "",
            "delivery_channel": channel,
        })
    return result


# ============================================================
# 扩源 HU-eun/campus-recruitment 的字段转换（中文字段名）
# ============================================================
def hueun_cohort_ok(c):
    """面向 2027 届（或届别不限）才收；纯 2026/2025 届不要"""
    c = str(c or "")
    if not c:
        return True
    return "2027" in c or "不限" in c


def hueun_city(loc_list):
    """工作地点是城市列表：多处/全国 → 全国；单个城市清洗后取第一个"""
    if not loc_list:
        return "未公布"
    locs = [str(x).strip() for x in loc_list if str(x).strip()] if isinstance(loc_list, list) else [str(loc_list)]
    if not locs:
        return "未公布"
    for x in locs:
        if x in ("全国", "全国各地", "全国多地"):
            return "全国"
    for x in locs:
        for w in OVERSEAS_WORDS:
            if w in x:
                return "海外"
    if len(locs) >= 4:
        return "全国"
    return clean_city(locs[:1])


def hueun_batch_ok(batch):
    b = str(batch or "")
    return any(k in b for k in ("秋招", "实习", "春招"))


def hueun_industry(v):
    """把扩源的行业大类归一到主源的赛道名（页面的标签配色和 AI 匹配都按这套名字工作）"""
    s = str(v[0] if isinstance(v, list) and v else (v or "")).strip()
    if not s:
        return "其他"
    if any(k in s for k in ("互联网", "IT", "软件", "游戏", "人工智能")):
        return "互联网"
    if any(k in s for k in ("半导体", "电子", "通信")):
        return "半导体/硬件"
    if any(k in s for k in ("金融", "银行", "证券", "基金", "保险", "信托", "投融")):
        return "银行/金融"
    if any(k in s for k in ("军工", "国防", "航天", "航空")):
        return "军工/研究所"
    if any(k in s for k in ("新能源", "电池")):
        return "新能源车企"
    if "汽车" in s or "车企" in s:
        return "传统车企"
    if any(k in s for k in ("医药", "医疗", "生物")):
        return "医药/生物"
    if any(k in s for k in ("快消", "消费", "零售", "电商")):
        return "快消/零售"
    if any(k in s for k in ("能源", "化工", "环保", "电力", "石油")):
        return "央国企"
    if any(k in s for k in ("房地产", "建筑", "土木")):
        return "央国企"
    if any(k in s for k in ("国企", "央企")):
        return "央国企"
    if any(k in s for k in ("高校", "教育", "事业单位")):
        return "高校/事业单位"
    if "制造" in s:
        return "制造业"
    return s[:12]


def convert_hueun(raw_jobs, today):
    result = []
    for item in raw_jobs:
        if not isinstance(item, dict):
            continue
        company = str(item.get("公司名称") or "").strip()
        if not company or "筛选前必看" in company or "点这里" in company:
            continue                      # 数据源里的说明行，不是真岗位

        cohort = item.get("招聘对象")
        cohort_str = " / ".join(cohort) if isinstance(cohort, list) else str(cohort or "")
        if not hueun_cohort_ok(cohort_str):
            continue

        batch_raw = item.get("批次")
        batch = " / ".join(batch_raw) if isinstance(batch_raw, list) else str(batch_raw or "")
        if not hueun_batch_ok(batch):
            continue
        # 归一化成三大类：实习类统一叫"实习"，春招类叫"春招"，其余算秋招
        if "实习" in batch:
            batch = "实习"
        elif "春招" in batch:
            batch = "春招"
        elif "提前批" in batch:
            batch = "秋招提前批"
        else:
            batch = "秋招"

        positions = split_positions(item.get("招聘岗位"))
        if positions:
            title = "%s %s" % (cohort_str or "2027届", "、".join(positions[:3]))
        else:
            title = "%s %s类岗位" % (cohort_str or "2027届", str(item.get("行业大类") or "综合")[:6])

        deadline_raw = str(item.get("截止时间") or "").strip()
        deadline = normalize_deadline(deadline_raw)
        d = parse_date(deadline_raw)
        ended = bool(d and d < today)
        updated = str(item.get("更新时间") or "").strip()
        ud = parse_date(updated)
        updated_iso = ud.isoformat() if ud else updated[:10]

        # 旧岗位只要还开放就收；已结束的只收 9 月以来的（再旧的没意义）
        if ended and (not ud or ud < KEEP_ENDED_SINCE):
            continue

        # 链接三档：官网直链 > 官方公告页 > 搜索兜底（页面上处理）
        apply_u = str(item.get("投递方式") or "").strip()
        official_url = apply_u if (is_http(apply_u) and not is_weixin(apply_u)) else ""
        notice_u = str(item.get("官方公告") or "").strip()
        info_url = notice_u if is_http(notice_u) else ""

        if official_url:
            channel = "官网"
        elif is_weixin(apply_u) or is_weixin(notice_u):
            channel = "公众号"
        elif "@" in (str(item.get("备注/提示") or "") + apply_u):
            channel = "邮箱"
        else:
            channel = "其他"

        industry = item.get("行业大类")
        industry = hueun_industry(industry)

        result.append({
            "company": company,
            "title": title,
            "positions": positions,
            "program": str(item.get("备注/提示") or "")[:30],
            "city": hueun_city(item.get("工作地点")),
            "type": industry[:12],
            "batch": batch,
            "deadline": deadline,
            "updated": updated_iso,
            "isNew": is_new(updated_iso, today),
            "ended": ended,
            "official_url": official_url,
            "info_url": info_url,
            "delivery_channel": channel,
        })
    return result


def dedupe(jobs):
    """按 公司+截止+岗位方向前缀 去重（主源优先，扩源补充）"""
    seen = set()
    out = []
    for j in jobs:
        key = (j["company"], j["deadline"], j["title"][:20])
        if key in seen:
            continue
        seen.add(key)
        out.append(j)
    return out


def write_data_js(jobs):
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            old = f.read()
        with open(DATA_FILE + ".bak", "w", encoding="utf-8") as f:
            f.write(old)

    header = (
        "// 数据文件：由 spider.py 自动生成，不需要手工编辑\n"
        "// 数据来源：GitHub 开源项目 xixicc186/xixicc2027 + HU-eun/campus-recruitment（每日更新）\n"
        "// 生成时间：%s\n"
        % datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    )
    # 不加缩进，控制文件体积（数据由机器人维护，不需要人工可读）
    body = "var JOBS = " + json.dumps(jobs, ensure_ascii=False, separators=(",", ":")) + ";\n"
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        f.write(header + body)


def main():
    today = datetime.date.today()

    raw1 = fetch_json(SOURCES_XIXICC, timeout=40, cache_name=".cache-xixicc.json") or []
    raw2 = fetch_json(SOURCES_HUEUN, timeout=300, cache_name=".cache-hueun.json") or []

    print("主源原始 %d 条，扩源原始 %d 条" % (len(raw1), len(raw2)))
    jobs = convert_xixicc(raw1, today) + convert_hueun(raw2, today)
    jobs = dedupe(jobs)
    print("合并去重后 %d 条" % len(jobs))

    added = enrich_official_sites(jobs)
    print("官方招聘网对照表补充链接 %d 条" % added)

    write_data_js(jobs)
    print("data.js 更新完成！备份已保存为 data.js.bak")

    ended = sum(1 for j in jobs if j["ended"])
    clickable = sum(1 for j in jobs if j["official_url"])
    notice = sum(1 for j in jobs if not j["official_url"] and j["info_url"])
    print("其中已结束 %d 条；官网直链 %d 条；公告链接 %d 条" % (ended, clickable, notice))

    rails = {"秋招": 0, "实习": 0, "春招": 0, "其他": 0}
    for j in jobs:
        if j["batch"] == "实习":
            rails["实习"] += 1
        elif "春" in j["batch"]:
            rails["春招"] += 1
        else:
            rails["秋招"] += 1
    print("分类分布:", rails)


if __name__ == "__main__":
    main()
