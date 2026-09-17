# -*- coding: utf-8 -*-
"""
chem-research-loop / domains.py
===============================
四个化学方向的领域策略（策略模式）+ 通用同义词归并表。

设计要点：
  同一套引擎，四份可插拔配置 —— 而不是四套代码。
  ChemistryDomain 抽象基类定义契约，子类只提供「领域知识」：
    · query_templates  —— 该方向的检索词骨架
    · concept_seeds    —— 分四类的概念规范名（材料 / 方法 / 应用 / 指标）
    · synonyms         —— 同义词归并表（见下）
    · metrics          —— 该方向关心的量化指标
    · hypothesis_frames—— 假设句式模板

为什么必须做同义词归并
----------------------
  「MOF」「MOFs」「metal-organic framework」「metal–organic framework」
  在文献统计上必须是同一个概念。若不归并，会同时引发三个错误：
    1. 频次被稀释 —— 本该 100 次的 MOF 被拆成 4 个各 25 次的概念，
       于是没有任何概念能跨过统计门槛，空白探测直接失效；
    2. 候选重复 —— 空白列表里会同时出现「MOF × X」和
       「metal-organic framework × X」，实为同一条；
    3. 命中率偏低 —— 只写全称就漏掉所有用缩写的文献，反之亦然。

「研究空白」在这里被定义为可计算的问题
--------------------------------------
  概念归入四个语义类别后，跨类别的「未共现组合」就是候选空白。
  例如 material × application 的笛卡尔积中，若某组合在全部文献里
  共现次数显著低于期望，它就是一个结构洞式的空白点。
  这让空白探测从「凭感觉猜」变成「有算法、有证据、可复核」。
"""

from __future__ import annotations

import re

CONCEPT_CATEGORIES = ("material", "method", "application", "metric")

CATEGORY_LABEL_ZH = {
    "material": "材料体系",
    "method": "方法技术",
    "application": "应用场景",
    "metric": "性能指标",
}


# --------------------------------------------------------------------------
# 同义词归并表：规范名 -> 书写变体
# --------------------------------------------------------------------------

COMMON_SYNONYMS: dict[str, list[str]] = {
    # ---------------- 材料体系 ----------------
    "MOF": ["MOFs", "metal-organic framework", "metal-organic frameworks",
            "metal organic framework", "metal organic frameworks"],
    "COF": ["COFs", "covalent organic framework", "covalent organic frameworks"],
    "ZIF": ["ZIFs", "zeolitic imidazolate framework", "zeolitic imidazolate frameworks"],
    "MXene": ["MXenes"],
    "graphene": ["graphenes", "graphene sheet", "graphene sheets"],
    "graphene oxide": ["GO", "graphene oxides"],
    "carbon nanotube": ["carbon nanotubes", "CNT", "CNTs",
                        "multi-walled carbon nanotube", "single-walled carbon nanotube"],
    "perovskite": ["perovskites", "halide perovskite", "perovskite solar cell"],
    "solid electrolyte": ["solid electrolytes", "solid-state electrolyte", "solid-state electrolytes"],
    "single-atom catalyst": ["single-atom catalysts", "single atom catalyst", "SAC", "SACs"],
    "ionic liquid": ["ionic liquids", "ILs"],
    "quantum dot": ["quantum dots", "QDs"],
    "nanoparticle": ["nanoparticles", "NPs"],
    "nanocluster": ["nanoclusters", "metal nanocluster", "metal nanoclusters"],
    "gold nanoparticle": ["gold nanoparticles", "AuNP", "AuNPs"],
    "silver nanoparticle": ["silver nanoparticles", "AgNP", "AgNPs"],
    "molecularly imprinted polymer": ["molecularly imprinted polymers", "MIP", "MIPs"],
    "aptamer": ["aptamers"],
    "antibody": ["antibodies"],
    "DNAzyme": ["DNAzymes"],
    "porphyrin": ["porphyrins"],
    "activated carbon": ["activated carbons"],
    "porous carbon": ["porous carbons"],
    "layered double hydroxide": ["layered double hydroxides", "LDH"],
    "polyoxometalate": ["polyoxometalates", "POM", "POMs"],
    "high-entropy alloy": ["high entropy alloy", "high-entropy alloys", "HEA", "HEAs"],
    "lithium iron phosphate": ["LFP", "LiFePO4"],
    "nickel-rich cathode": ["Ni-rich cathode", "nickel rich cathode"],
    "silicon anode": ["Si anode", "silicon anodes"],
    "sodium-ion": ["sodium-ion battery", "sodium ion battery", "Na-ion battery"],
    "deep eutectic solvent": ["deep eutectic solvents", "DES"],
    "photonic crystal": ["photonic crystals"],
    "magnetic bead": ["magnetic beads", "Fe3O4 nanoparticles"],
    "conducting polymer": ["conducting polymers", "PEDOT", "polyaniline"],
    "transition metal complex": ["transition metal complexes"],
    "metal surface": ["metal surfaces"],
    "oxide surface": ["oxide surfaces"],
    "aqueous solution": ["aqueous solutions", "in aqueous solution"],
    "excited state": ["excited states", "excited-state"],
    "radical": ["radicals", "free radical", "free radicals", "radical species"],
    "enzyme active site": ["enzyme active sites"],
    "protein-ligand complex": ["protein-ligand complexes"],
    "solvated electron": ["solvated electrons", "hydrated electron"],
    "photocatalyst": ["photocatalysts"],
    "organocatalyst": ["organocatalysts"],
    "phosphine ligand": ["phosphine ligands", "phosphines"],
    "NHC ligand": ["NHC ligands", "N-heterocyclic carbene", "N-heterocyclic carbenes"],
    "chiral ligand": ["chiral ligands", "chiral phosphine"],
    "Brønsted acid": ["Bronsted acid", "Brønsted acids", "Bronsted acids"],
    "Lewis acid": ["Lewis acids"],
    "phase-transfer catalyst": ["phase transfer catalyst"],
    "bipyridine": ["bipyridines", "bpy"],

    # ---------------- 方法技术 ----------------
    "density functional theory": ["DFT", "DFT calculations", "DFT-D", "DFT study"],
    "machine learning": ["ML", "machine-learning"],
    "molecular dynamics": ["MD", "MD simulation", "MD simulations"],
    "ab initio molecular dynamics": ["AIMD"],
    "neural network potential": ["NNP", "NNPs", "neural network potentials"],
    "machine learning potential": ["MLP", "MLPs", "machine-learned potential",
                                   "machine learning potentials"],
    "Gaussian approximation potential": ["GAP"],
    "QM/MM": ["QM-MM", "quantum mechanics/molecular mechanics"],
    "transition state search": ["transition state", "transition states"],
    "NEB": ["nudged elastic band"],
    "free energy perturbation": ["FEP"],
    "semiempirical": ["semi-empirical"],
    "basis set": ["basis sets"],
    "hybrid functional": ["hybrid functionals", "hybrid DFT"],
    "dispersion correction": ["dispersion corrections", "D3 correction", "Grimmme D3"],
    "implicit solvation": ["implicit solvent model", "PCM", "COSMO"],
    "active learning": ["active-learning"],
    "foundation model": ["foundation models"],
    "ab initio": ["ab-initio"],
    "coupled cluster": ["CCSD", "CCSD(T)"],
    "high-throughput screening": ["high throughput screening", "HTS",
                                 "high-throughput experimentation"],
    "in situ characterization": ["in-situ characterization", "operando characterization"],
    "X-ray diffraction": ["XRD", "PXRD", "powder X-ray diffraction"],
    "X-ray absorption spectroscopy": ["XAFS", "XANES", "EXAFS"],
    "solid-state NMR": ["ssNMR"],
    "neutron diffraction": ["neutron scattering"],
    "electrospinning": ["electrospun"],
    "solvothermal synthesis": ["solvothermal", "solvothermal method"],
    "hydrothermal synthesis": ["hydrothermal", "hydrothermal method"],
    "atomic layer deposition": ["ALD"],
    "chemical vapor deposition": ["CVD"],
    "post-synthetic modification": ["post-synthetic modifications", "PSM",
                                    "post synthetic modification"],
    "defect engineering": ["defect-engineered", "defect engineering strategy"],
    "doping": ["doped", "heteroatom doping"],
    "interface engineering": ["interface-engineered"],
    "heterostructure": ["heterostructures"],
    "electrocatalysis": ["electrocatalytic"],
    "photocatalysis": ["photocatalytic"],
    "grand canonical Monte Carlo": ["GCMC"],
    "in situ TEM": ["in-situ TEM"],
    "cross-coupling": ["cross coupling", "cross-couplings"],
    "Suzuki coupling": ["Suzuki reaction", "Suzuki-Miyaura", "Suzuki-Miyaura coupling"],
    "Heck reaction": ["Heck coupling", "Mizoroki-Heck"],
    "Sonogashira": ["Sonogashira coupling", "Sonogashira reaction"],
    "Buchwald-Hartwig amination": ["Buchwald-Hartwig coupling", "Buchwald-Hartwig"],
    "C-H activation": ["C-H functionalization", "C-H bond activation"],
    "asymmetric catalysis": ["asymmetric catalytic", "enantioselective catalysis"],
    "photoredox catalysis": ["photoredox", "visible-light photoredox"],
    "flow chemistry": ["continuous flow", "flow synthesis"],
    "microwave": ["microwave-assisted", "microwave irradiation"],
    "mechanochemistry": ["mechanochemical"],
    "ring-closing metathesis": ["RCM"],
    "click chemistry": ["click reaction", "CuAAC"],
    "biocatalysis": ["biocatalytic"],
    "directed evolution": ["directed-evolution"],
    "fluorescence": ["fluorescent", "fluorescent probe", "fluorescence detection"],
    "electrochemiluminescence": ["ECL"],
    "surface-enhanced Raman": ["SERS", "surface enhanced Raman",
                              "surface-enhanced Raman scattering"],
    "electrochemical sensing": ["electrochemical sensor", "electrochemical sensors",
                                "electrochemical detection", "electrochemical assay"],
    "colorimetric assay": ["colorimetric", "colorimetric detection", "colorimetry"],
    "lateral flow assay": ["lateral flow assays", "LFA", "lateral-flow immunoassay"],
    "surface plasmon resonance": ["SPR"],
    "mass spectrometry": ["MS"],
    "LC-MS": ["LC-MS/MS", "liquid chromatography-mass spectrometry"],
    "HPLC": ["high-performance liquid chromatography"],
    "ICP-MS": ["inductively coupled plasma mass spectrometry"],
    "gas chromatography": ["GC", "GC-MS"],
    "paper-based device": ["paper-based devices", "paper-based sensor"],
    "microfluidic": ["microfluidics", "microfluidic device"],
    "wearable sensor": ["wearable sensors"],
    "field-effect transistor": ["FET", "field effect transistor"],
    "impedance spectroscopy": ["EIS", "electrochemical impedance spectroscopy"],
    "photoelectrochemical": ["PEC"],

    # ---------------- 应用场景 ----------------
    "CO2 capture": ["carbon capture", "CO2 sequestration", "carbon dioxide capture",
                    "CO2 adsorption", "CO2 separation"],
    "CO2 conversion": ["CO2 reduction", "CO2RR", "carbon dioxide conversion",
                       "CO2 electroreduction", "CO2 hydrogenation"],
    "oxygen evolution reaction": ["OER"],
    "hydrogen evolution reaction": ["HER"],
    "oxygen reduction reaction": ["ORR"],
    "nitrogen reduction": ["nitrogen reduction reaction", "NRR"],
    "battery": ["batteries", "lithium-ion battery", "Li-ion battery",
                "lithium ion battery", "lithium metal battery"],
    "supercapacitor": ["supercapacitors", "supercapacitive"],
    "fuel cell": ["fuel cells"],
    "solar cell": ["solar cells", "photovoltaic", "photovoltaics"],
    "drug delivery": ["drug-delivery", "drug carrier"],
    "gas separation": ["gas separations", "gas-separation"],
    "membrane separation": ["membrane-based separation", "membrane separations"],
    "water harvesting": ["atmospheric water harvesting", "water adsorption"],
    "seawater desalination": ["desalination", "seawater desalination"],
    "sensing": ["sensor", "sensors", "detection"],
    "hydrogen storage": ["H2 storage"],
    "methane storage": ["CH4 storage"],
    "ammonia synthesis": ["NH3 synthesis", "nitrogen fixation"],
    "catalysis": ["catalytic reaction"],
    "thermal management": ["heat management"],
    "food safety": ["food-safety", "food analysis"],
    "environmental monitoring": ["environmental analysis", "environmental detection"],
    "clinical diagnosis": ["clinical diagnostics", "clinical detection", "disease diagnosis"],
    "point-of-care testing": ["point-of-care", "POC testing", "point of care"],
    "heavy metal detection": ["heavy metal ions detection", "heavy metal sensing"],
    "pesticide detection": ["pesticide residue detection", "pesticide sensing"],
    "pathogen detection": ["bacterial detection", "pathogen sensing"],
    "biomarker detection": ["biomarker sensing"],
    "drug residue detection": ["antibiotic detection", "drug residues detection"],
    "mycotoxin detection": ["mycotoxin sensing", "aflatoxin detection"],
    "explosive detection": ["explosive sensing", "TNT detection"],
    "gas sensing": ["gas sensor", "gas sensors"],
    "intracellular imaging": ["cell imaging", "cellular imaging"],
    "single-cell analysis": ["single cell analysis"],
    "water quality": ["water-quality"],
    "pharmaceutical synthesis": ["drug synthesis", "API synthesis"],
    "drug discovery": ["drug development"],
    "total synthesis": ["total syntheses"],
    "late-stage functionalization": ["late-stage C-H functionalization", "late stage functionalization"],
    "polymer synthesis": ["polymerization"],
    "natural product": ["natural products"],
    "agrochemical": ["agrochemicals"],
    "catalyst design": ["catalyst discovery", "catalyst development"],
    "reaction mechanism": ["reaction mechanisms", "mechanistic study"],
    "materials discovery": ["materials design", "materials screening"],
    "spectra prediction": ["spectrum prediction", "spectra simulation"],
    "photochemistry": ["photochemical"],
    "surface reaction": ["surface reactions"],
    "enzyme engineering": ["enzyme design"],
    "thermochemistry": ["thermochemical"],
    "solubility prediction": ["solubility"],
    "phase diagram prediction": ["phase diagram", "phase diagrams"],
    "battery electrolyte design": ["electrolyte design"],
    "drug design": ["rational drug design"],

    # ---------------- 性能指标 ----------------
    "selectivity": ["selectivities", "high selectivity", "selective adsorption"],
    "capacity": ["capacities", "uptake capacity", "adsorption capacity", "adsorption capacities"],
    "stability": ["stabilities", "chemical stability", "thermal stability",
                  "hydrothermal stability", "durability"],
    "conductivity": ["ionic conductivity", "electrical conductivity", "thermal conductivity"],
    "overpotential": ["overpotentials"],
    "faradaic efficiency": ["Faradaic efficiency"],
    "turnover frequency": ["TOF"],
    "turnover number": ["TON"],
    "surface area": ["BET surface area", "specific surface area", "SSA"],
    "pore volume": ["pore volumes"],
    "cyclability": ["cycling stability", "cycle stability", "cycling performance"],
    "rate capability": ["rate performance"],
    "coulombic efficiency": ["Coulombic efficiency"],
    "band gap": ["bandgap", "band gaps", "optical band gap"],
    "adsorption enthalpy": ["isosteric heat of adsorption", "heat of adsorption"],
    "working capacity": ["working capacities", "usable capacity"],
    "regenerability": ["regeneration", "recyclability", "reusability"],
    "yield": ["yields", "isolated yield", "chemical yield"],
    "enantioselectivity": ["enantioselectivities", "enantiomeric excess", "ee value"],
    "diastereoselectivity": ["diastereoselectivities"],
    "chemoselectivity": ["chemoselectivities"],
    "regioselectivity": ["regioselectivities"],
    "substrate scope": ["substrate scopes", "substrate tolerance"],
    "functional group tolerance": ["functional-group tolerance"],
    "atom economy": ["atom-economical", "atom economical"],
    "scalability": ["scale-up", "gram-scale", "large-scale synthesis"],
    "limit of detection": ["LOD", "detection limit", "limits of detection", "detection limits"],
    "sensitivity": ["sensitivities", "high sensitivity"],
    "linear range": ["linear ranges", "linear detection range"],
    "recovery": ["recoveries", "spike recovery"],
    "anti-interference": ["anti-interference ability", "interference resistance"],
    "reproducibility": ["repeatability"],
    "specificity": ["high specificity"],
    "response time": ["response times", "response speed"],
    "signal-to-noise": ["signal to noise", "S/N"],
    "MAE": ["mean absolute error", "mean absolute errors"],
    "RMSE": ["root mean square error", "root-mean-square error"],
    "activation barrier": ["activation energy", "activation barriers", "energy barrier"],
    "binding energy": ["binding energies", "adsorption energy", "adsorption energies"],
    "reaction energy": ["reaction energies"],
    "free energy": ["free energies", "Gibbs free energy"],
    "computational cost": ["computational costs", "computational expense"],
    "transferability": ["transferable"],
    "extrapolation": ["extrapolative"],
    "wall time": ["wall-time"],
}


# --------------------------------------------------------------------------
# 抽象基类
# --------------------------------------------------------------------------

class ChemistryDomain:
    """化学研究方向的抽象基类。子类只填领域知识，不写逻辑。"""

    key = "base"
    name_zh = "通用化学"
    summary = ""
    query_templates: list[str] = []
    concept_seeds: dict[str, list[str]] = {}
    synonyms: dict[str, list[str]] = {}
    metrics: list[str] = []
    primary_sources: list[str] = []
    hypothesis_frames: list[str] = []

    # 空白探测考察的类别组合（笛卡尔积对象）
    gap_pairs: list[tuple[str, str]] = [
        ("material", "application"),
        ("method", "application"),
    ]

    @classmethod
    def build_queries(cls, topic: str, limit: int = 4) -> list[str]:
        """把用户主题套进该方向的检索词骨架，产出多角度查询串。"""
        out: list[str] = []
        seen: set[str] = set()
        topic_clean = topic.strip()
        if topic_clean:
            out.append(topic_clean)
            seen.add(topic_clean.lower())
        for tpl in cls.query_templates:
            if len(out) >= limit:
                break
            q = tpl.format(topic=topic_clean).strip()
            k = q.lower()
            if q and k not in seen:
                seen.add(k)
                out.append(q)
        return out

    @classmethod
    def merged_synonyms(cls) -> dict[str, list[str]]:
        merged = dict(COMMON_SYNONYMS)
        merged.update(cls.synonyms)
        return merged

    @classmethod
    def term_index(cls) -> dict[str, list[tuple[str, list[re.Pattern]]]]:
        """预编译词典：{类别: [(规范名, [各变体的匹配正则])]}。"""
        syn = cls.merged_synonyms()
        index: dict[str, list[tuple[str, list[re.Pattern]]]] = {}
        for cat in CONCEPT_CATEGORIES:
            items: list[tuple[str, list[re.Pattern]]] = []
            for term in cls.concept_seeds.get(cat, []):
                variants = [term] + syn.get(term, [])
                pats = [compile_term(v) for v in dict.fromkeys(variants)]
                items.append((term, pats))
            index[cat] = items
        return index

    @classmethod
    def to_dict(cls) -> dict:
        return {
            "key": cls.key,
            "name_zh": cls.name_zh,
            "summary": cls.summary,
            "metrics": cls.metrics,
            "primary_sources": cls.primary_sources,
            "concept_counts": {c: len(cls.concept_seeds.get(c, [])) for c in CONCEPT_CATEGORIES},
            "gap_pairs": [f"{a}×{b}" for a, b in cls.gap_pairs],
        }


def compile_term(term: str) -> re.Pattern:
    """把术语编译为词边界正则。

    兼容空格 / 连字符 / 斜杠 / 破折号的多种书写变体，例如
    'metal-organic framework' 可匹配 'metal organic framework'、
    'Metal-Organic Framework'、'metal–organic framework'（en dash）。
    """
    parts = [re.escape(p) for p in re.split(r"[\s\-–—/]+", term.strip()) if p]
    if not parts:
        return re.compile(r"(?!x)x")
    body = r"[\s\-–—/]+".join(parts)
    return re.compile(r"(?<![A-Za-z0-9])" + body + r"(?![A-Za-z0-9])", re.IGNORECASE)


# --------------------------------------------------------------------------
# 方向一：材料化学
# --------------------------------------------------------------------------

class MaterialsChemistry(ChemistryDomain):
    key = "materials"
    name_zh = "材料化学"
    summary = "MOF、沸石、钙钛矿、能源与催化材料。文献数据最密集，公开库覆盖最好。"
    primary_sources = ["openalex", "crossref", "europepmc", "arxiv"]

    query_templates = [
        "{topic} synthesis and characterization",
        "{topic} performance and stability",
        "machine learning for {topic}",
        "{topic} review",
        "{topic} mechanism",
    ]

    gap_pairs = [
        ("material", "application"),
        ("method", "application"),
        ("material", "method"),
        ("application", "metric"),
    ]

    metrics = [
        "吸附容量 / uptake capacity", "选择性 / selectivity", "比表面积 / BET surface area",
        "循环稳定性 / cycling stability", "产率 / yield", "过电位 / overpotential",
        "能量密度 / energy density", "孔径 / pore size",
    ]

    hypothesis_frames = [
        "若将 {modifier} 引入 {material} 体系，则 {metric} 有望因 {mechanism} 而显著提升",
        "把 {method} 应用于 {material}，可在保持 {metric_a} 的同时改善 {metric_b}",
        "{material} × {application} 的交叉组合目前缺乏系统性研究，其可行性值得验证",
    ]

    concept_seeds = {
        "material": [
            "MOF", "ZIF", "COF", "zeolite", "perovskite", "MXene", "graphene",
            "graphene oxide", "carbon nanotube", "porous carbon", "activated carbon",
            "layered double hydroxide", "polyoxometalate", "high-entropy alloy",
            "single-atom catalyst", "nanoparticle", "nanocluster", "quantum dot",
            "solid electrolyte", "lithium iron phosphate", "nickel-rich cathode",
            "silicon anode", "sodium-ion", "ionic liquid", "deep eutectic solvent",
        ],
        "method": [
            "solvothermal synthesis", "hydrothermal synthesis", "electrospinning",
            "atomic layer deposition", "chemical vapor deposition",
            "in situ characterization", "X-ray diffraction", "neutron diffraction",
            "X-ray absorption spectroscopy", "solid-state NMR",
            "high-throughput screening", "machine learning", "density functional theory",
            "grand canonical Monte Carlo", "post-synthetic modification",
            "defect engineering", "doping", "interface engineering", "heterostructure",
            "electrocatalysis", "photocatalysis", "in situ TEM",
        ],
        "application": [
            "CO2 capture", "CO2 conversion", "gas separation", "membrane separation",
            "hydrogen storage", "methane storage", "water harvesting",
            "oxygen evolution reaction", "hydrogen evolution reaction",
            "oxygen reduction reaction", "nitrogen reduction", "battery",
            "supercapacitor", "fuel cell", "solar cell", "drug delivery",
            "catalysis", "ammonia synthesis", "seawater desalination", "sensing",
            "thermal management",
        ],
        "metric": [
            "selectivity", "capacity", "stability", "conductivity", "overpotential",
            "faradaic efficiency", "turnover frequency", "surface area", "pore volume",
            "cyclability", "rate capability", "coulombic efficiency", "band gap",
            "adsorption enthalpy", "working capacity", "regenerability",
        ],
    }


# --------------------------------------------------------------------------
# 方向二：有机合成方法学
# --------------------------------------------------------------------------

class OrganicSynthesis(ChemistryDomain):
    key = "synthesis"
    name_zh = "有机合成方法学"
    summary = "反应条件、底物适用范围、催化循环。数据分散在正文，抽取需更细。"
    primary_sources = ["crossref", "openalex", "europepmc"]

    query_templates = [
        "{topic} reaction conditions optimization",
        "{topic} substrate scope",
        "catalytic {topic} mechanism",
        "{topic} enantioselective",
    ]

    gap_pairs = [
        ("method", "application"),
        ("method", "metric"),
        ("material", "method"),
        ("application", "metric"),
    ]

    metrics = [
        "产率 / yield", "对映选择性 / ee", "非对映选择性 / dr", "转化率 / conversion",
        "周转数 / TON", "底物范围 / substrate scope", "克级放大 / scale-up",
    ]

    hypothesis_frames = [
        "将 {ligand} 与 {metal} 组合用于 {reaction}，有望同时提升 {metric} 并拓宽底物范围",
        "在 {reaction} 中引入 {strategy}，可能绕开 {limitation} 这一长期瓶颈",
        "{catalyst_type} 催化的 {reaction} 在 {substrate_class} 底物上应保持高 {metric}",
    ]

    concept_seeds = {
        "material": [
            "palladium", "nickel", "copper", "rhodium", "iridium", "ruthenium",
            "iron", "cobalt", "photocatalyst", "organocatalyst", "phosphine ligand",
            "NHC ligand", "chiral ligand", "bipyridine", "porphyrin",
            "Brønsted acid", "Lewis acid", "phase-transfer catalyst",
        ],
        "method": [
            "cross-coupling", "Suzuki coupling", "Heck reaction", "Sonogashira",
            "Buchwald-Hartwig amination", "C-H activation", "asymmetric catalysis",
            "photoredox catalysis", "electrocatalysis", "flow chemistry",
            "microwave", "mechanochemistry", "ring-closing metathesis",
            "click chemistry", "biocatalysis", "directed evolution",
            "high-throughput screening",
        ],
        "application": [
            "pharmaceutical synthesis", "drug discovery", "total synthesis",
            "late-stage functionalization", "polymer synthesis", "natural product",
            "agrochemical", "materials discovery", "isotope labeling",
            "peptide synthesis", "oligosaccharide synthesis", "catalysis",
        ],
        "metric": [
            "yield", "enantioselectivity", "diastereoselectivity", "turnover number",
            "turnover frequency", "chemoselectivity", "substrate scope",
            "functional group tolerance", "scalability", "atom economy",
            "regioselectivity",
        ],
    }


# --------------------------------------------------------------------------
# 方向三：计算化学与分子模拟
# --------------------------------------------------------------------------

class ComputationalChemistry(ChemistryDomain):
    key = "computational"
    name_zh = "计算化学与分子模拟"
    summary = "DFT、分子动力学、机器学习势函数。方法学更新快，预印本占比高。"
    primary_sources = ["arxiv", "openalex", "crossref"]

    query_templates = [
        "{topic} density functional theory",
        "{topic} machine learning potential",
        "{topic} molecular dynamics simulation",
        "benchmark {topic}",
    ]

    gap_pairs = [
        ("method", "material"),
        ("method", "application"),
        ("method", "metric"),
        ("material", "application"),
    ]

    metrics = [
        "误差 / MAE·RMSE", "计算成本 / cost", "泛化能力 / transferability",
        "外推能力 / extrapolation", "可复现性 / reproducibility", "自由能精度 / kcal·mol",
    ]

    hypothesis_frames = [
        "在 {system} 上用 {method} 替代 {baseline}，有望在 {metric} 相近的前提下把成本降低一个量级",
        "把 {descriptor} 引入机器学习势，应能改善对 {property} 的外推表现",
        "{functional_class} 在 {system_class} 上的系统误差可能存在可校正的结构性规律",
    ]

    concept_seeds = {
        "material": [
            "transition metal complex", "enzyme active site", "zeolite", "perovskite",
            "metal surface", "oxide surface", "nanocluster", "aqueous solution",
            "ionic liquid", "solvated electron", "excited state", "radical",
            "protein-ligand complex",
        ],
        "method": [
            "density functional theory", "ab initio", "coupled cluster", "MP2",
            "Hartree-Fock", "molecular dynamics", "ab initio molecular dynamics",
            "metadynamics", "umbrella sampling", "neural network potential",
            "machine learning potential", "Gaussian approximation potential", "QM/MM",
            "transition state search", "NEB", "free energy perturbation",
            "semiempirical", "basis set", "hybrid functional", "dispersion correction",
            "implicit solvation", "active learning", "foundation model",
            "grand canonical Monte Carlo",
        ],
        "application": [
            "catalyst design", "reaction mechanism", "drug design",
            "materials discovery", "spectra prediction", "photochemistry",
            "battery electrolyte design", "surface reaction", "enzyme engineering",
            "phase diagram prediction", "thermochemistry", "solubility prediction",
        ],
        "metric": [
            "MAE", "RMSE", "activation barrier", "binding energy", "reaction energy",
            "band gap", "free energy", "computational cost", "transferability",
            "extrapolation", "wall time",
        ],
    }


# --------------------------------------------------------------------------
# 方向四：分析化学与传感
# --------------------------------------------------------------------------

class AnalyticalChemistry(ChemistryDomain):
    key = "analytical"
    name_zh = "分析化学与传感"
    summary = "检测方法、传感材料、灵敏度与检出限。指标高度结构化，适合量化对比。"
    primary_sources = ["europepmc", "crossref", "openalex"]

    query_templates = [
        "{topic} detection limit and sensitivity",
        "{topic} sensor selectivity",
        "{topic} point-of-care detection",
        "{topic} real sample analysis",
    ]

    gap_pairs = [
        ("material", "application"),
        ("method", "application"),
        ("material", "method"),
        ("application", "metric"),
    ]

    metrics = [
        "检出限 / LOD", "灵敏度 / sensitivity", "线性范围 / linear range",
        "选择性 / selectivity", "响应时间 / response time", "回收率 / recovery",
        "抗干扰能力 / anti-interference",
    ]

    hypothesis_frames = [
        "以 {recognition_element} 修饰 {transducer_material}，有望把 {analyte} 的 LOD 推进至更低量级",
        "{strategy} 可能缓解 {interference} 对 {method} 的干扰，从而在真实样本中保持 {metric}",
        "把 {method_a} 与 {method_b} 联用，应可同时获得 {metric_a} 与 {metric_b} 的互补优势",
    ]

    concept_seeds = {
        "material": [
            "gold nanoparticle", "silver nanoparticle", "quantum dot", "nanocluster",
            "MOF", "MXene", "molecularly imprinted polymer", "aptamer", "antibody",
            "DNAzyme", "porphyrin", "graphene oxide", "magnetic bead",
            "conducting polymer", "photonic crystal",
        ],
        "method": [
            "fluorescence", "electrochemiluminescence", "surface-enhanced Raman",
            "electrochemical sensing", "colorimetric assay", "lateral flow assay",
            "surface plasmon resonance", "mass spectrometry", "LC-MS", "HPLC",
            "ICP-MS", "gas chromatography", "paper-based device", "microfluidic",
            "wearable sensor", "field-effect transistor", "impedance spectroscopy",
            "photoelectrochemical",
        ],
        "application": [
            "food safety", "environmental monitoring", "clinical diagnosis",
            "point-of-care testing", "drug residue detection", "heavy metal detection",
            "pesticide detection", "mycotoxin detection", "pathogen detection",
            "biomarker detection", "water quality", "explosive detection",
            "gas sensing", "intracellular imaging", "single-cell analysis",
        ],
        "metric": [
            "limit of detection", "sensitivity", "linear range", "selectivity",
            "response time", "recovery", "anti-interference", "reproducibility",
            "specificity", "signal-to-noise", "reusability",
        ],
    }


# --------------------------------------------------------------------------
# 注册表
# --------------------------------------------------------------------------

DOMAIN_REGISTRY: dict[str, type[ChemistryDomain]] = {
    MaterialsChemistry.key: MaterialsChemistry,
    OrganicSynthesis.key: OrganicSynthesis,
    ComputationalChemistry.key: ComputationalChemistry,
    AnalyticalChemistry.key: AnalyticalChemistry,
}

DOMAIN_ALIASES = {
    "materials": "materials", "material": "materials", "材料": "materials",
    "材料化学": "materials", "mof": "materials", "能源材料": "materials",
    "synthesis": "synthesis", "organic": "synthesis", "合成": "synthesis",
    "有机合成": "synthesis", "方法学": "synthesis", "催化": "synthesis",
    "computational": "computational", "compchem": "computational", "计算": "computational",
    "计算化学": "computational", "dft": "computational", "模拟": "computational",
    "analytical": "analytical", "analysis": "analytical", "分析": "analytical",
    "分析化学": "analytical", "传感": "analytical", "检测": "analytical",
}


def resolve_domain(name: str) -> type[ChemistryDomain] | None:
    """把用户输入的别名解析为方向类。支持英文、中文、常见缩写。"""
    if not name:
        return None
    key = DOMAIN_ALIASES.get(name.strip().lower(), name.strip().lower())
    return DOMAIN_REGISTRY.get(key)


def list_domains() -> str:
    lines = ["可用研究方向："]
    for k, cls in DOMAIN_REGISTRY.items():
        counts = "、".join(f"{CATEGORY_LABEL_ZH[c]} {len(cls.concept_seeds.get(c, []))}"
                          for c in CONCEPT_CATEGORIES)
        lines.append(f"  {k:<14} {cls.name_zh}")
        lines.append(f"  {'':<14} {cls.summary}")
        lines.append(f"  {'':<14} 词典：{counts}｜空白组合：{' '.join(a + '×' + b for a, b in cls.gap_pairs)}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(list_domains())
    print(f"\n通用同义词归并表：{len(COMMON_SYNONYMS)} 个规范名")
    print("\n=== 检索词生成示例（材料化学 / 主题=MOF CO2 capture）===")
    for q in MaterialsChemistry.build_queries("MOF CO2 capture"):
        print("  -", q)

    print("\n=== 同义词归并自检 ===")
    idx = MaterialsChemistry.term_index()
    samples = [
        ("material", "MOF", "The MOFs exhibit high uptake"),
        ("material", "MOF", "This metal-organic framework is stable"),
        ("application", "CO2 capture", "for carbon capture applications"),
        ("method", "density functional theory", "using DFT calculations"),
        ("metric", "selectivity", "shows excellent CO2/N2 selectivity"),
    ]
    for cat, term, text in samples:
        pats = [p for t, p in idx[cat] if t == term]
        hit = any(p.search(text) for p in (pats[0] if pats else []))
        print(f"  {term:<28} <- \"{text[:38]}\"  命中={hit}")
