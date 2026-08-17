import {
  Activity,
  BadgeCheck,
  Box,
  Boxes,
  Crosshair,
  Database,
  Dna,
  Droplets,
  Filter,
  Globe,
  Link2,
  Scissors,
  ShieldCheck,
  Target,
} from "lucide-react";
import type {
  JobStatus,
  PhaseDefinition,
  PhaseStatus,
  StepStatus,
  ToolInfo,
} from "@/types";

export const TOTAL_PHASES = 14;

export const PIPELINE_PHASES: PhaseDefinition[] = [
  {
    number: 1,
    name: "Proteome Retrieval & Redundancy Removal",
    icon: Database,
    steps: [
      { id: "1-1", number: 1, name: "Retrieve Proteome", tool: "UniProt" },
      { id: "1-2", number: 2, name: "Remove Redundant Sequences", tool: "CD-HIT" },
    ],
  },
  {
    number: 2,
    name: "Subtractive Proteomics Filtering",
    icon: Filter,
    steps: [
      { id: "2-1", number: 1, name: "Identify Essential Proteins", tool: "BLASTp + DEG" },
      { id: "2-2", number: 2, name: "Subcellular Localization", tool: "PSORTb" },
      { id: "2-3", number: 3, name: "Transmembrane Helix Prediction", tool: "DeepTMHMM" },
      { id: "2-4", number: 4, name: "Signal Peptide Prediction", tool: "Phobius" },
    ],
  },
  {
    number: 3,
    name: "Antigenicity & Safety Screening",
    icon: ShieldCheck,
    steps: [
      { id: "3-1", number: 1, name: "Allergenicity Check", tool: "AlgPred 2.0" },
      { id: "3-2", number: 2, name: "Antigenicity Prediction", tool: "VaxiJen" },
      { id: "3-3", number: 3, name: "Virulence Factor Check", tool: "BLASTp + VFDB" },
      { id: "3-4", number: 4, name: "Human Homology Check", tool: "BLASTp + Human Proteome" },
    ],
  },
  {
    number: 4,
    name: "Structural Prediction & Validation",
    icon: Boxes,
    steps: [
      { id: "4-1", number: 1, name: "Physicochemical Properties", tool: "ProtParam" },
      { id: "4-2", number: 2, name: "3D Structure Prediction", tool: "SwissModel" },
      { id: "4-3", number: 3, name: "Structure Quality Validation", tool: "ERRAT" },
      { id: "4-4", number: 4, name: "Secondary Structure Prediction", tool: "SOPMA" },
    ],
  },
  {
    number: 5,
    name: "CTL (CD8+ T-Cell) Epitope Prediction & Filtering",
    icon: Target,
    steps: [
      { id: "5-1", number: 1, name: "Predict CTL Epitopes", tool: "IEDB MHC-I" },
      { id: "5-2", number: 2, name: "CTL Antigenicity", tool: "VaxiJen" },
      { id: "5-3", number: 3, name: "CTL Allergenicity", tool: "AlgPred 2.0" },
      { id: "5-4", number: 4, name: "CTL Toxicity", tool: "ToxinPred" },
      { id: "5-5", number: 5, name: "CTL Immunogenicity", tool: "IEDB" },
    ],
  },
  {
    number: 6,
    name: "HTL (CD4+ T-Cell) Epitope Prediction & Filtering",
    icon: Crosshair,
    steps: [
      { id: "6-1", number: 1, name: "Predict HTL Epitopes", tool: "IEDB MHC-II" },
      { id: "6-2", number: 2, name: "IFN-γ Induction", tool: "IFNepitope" },
      { id: "6-3", number: 3, name: "IL-4 Induction", tool: "IL4Pred" },
      { id: "6-4", number: 4, name: "IL-10 Induction", tool: "IL10Pred" },
      { id: "6-5", number: 5, name: "HTL Antigenicity", tool: "VaxiJen" },
      { id: "6-6", number: 6, name: "HTL Allergenicity", tool: "AlgPred 2.0" },
      { id: "6-7", number: 7, name: "HTL Toxicity", tool: "ToxinPred" },
    ],
  },
  {
    number: 7,
    name: "B-Cell Epitope Prediction & Filtering",
    icon: Droplets,
    steps: [
      { id: "7-1", number: 1, name: "Linear B-Cell Epitopes", tool: "ABCpred" },
      { id: "7-2", number: 2, name: "B-Cell Antigenicity", tool: "VaxiJen" },
      { id: "7-3", number: 3, name: "B-Cell Allergenicity", tool: "AlgPred 2.0" },
      { id: "7-4", number: 4, name: "B-Cell Toxicity", tool: "ToxinPred" },
      { id: "7-5", number: 5, name: "Conformational B-Cell Epitopes", tool: "Ellipro" },
    ],
  },
  {
    number: 8,
    name: "Population Coverage & Epitope Overlap",
    icon: Globe,
    steps: [
      { id: "8-1", number: 1, name: "Population Coverage Analysis", tool: "IEDB-AR" },
      { id: "8-2", number: 2, name: "CTL-HTL Epitope Overlap Analysis", tool: "Custom" },
    ],
  },
  {
    number: 9,
    name: "MEV Construct Assembly",
    icon: Dna,
    steps: [
      { id: "9-1", number: 1, name: "Select Adjuvant", tool: "Library" },
      { id: "9-2", number: 2, name: "Assemble MEV Construct", tool: "BioPython" },
    ],
  },
  {
    number: 10,
    name: "MEV Construct Validation",
    icon: BadgeCheck,
    steps: [
      { id: "10-1", number: 1, name: "Physicochemical Properties", tool: "ProtParam" },
      { id: "10-2", number: 2, name: "Antigenicity", tool: "VaxiJen" },
      { id: "10-3", number: 3, name: "Allergenicity", tool: "AlgPred 2.0" },
      { id: "10-4", number: 4, name: "Toxicity", tool: "ToxinPred" },
      { id: "10-5", number: 5, name: "Solubility", tool: "Protein-Sol" },
    ],
  },
  {
    number: 11,
    name: "MEV 3D Structure & Validation",
    icon: Box,
    steps: [
      { id: "11-1", number: 1, name: "Secondary Structure", tool: "SOPMA" },
      { id: "11-2", number: 2, name: "3D Structure Prediction", tool: "AlphaFold/SwissModel" },
      { id: "11-3", number: 3, name: "Ramachandran Plot", tool: "MolProbity" },
      { id: "11-4", number: 4, name: "ERRAT Validation", tool: "ERRAT" },
      { id: "11-5", number: 5, name: "ProSA-web Validation", tool: "ProSA-web" },
    ],
  },
  {
    number: 12,
    name: "Disulfide Bond Engineering",
    icon: Link2,
    steps: [
      { id: "12-1", number: 1, name: "Design Disulfide Bonds", tool: "DbD2" },
    ],
  },
  {
    number: 13,
    name: "Codon Optimization & In Silico Cloning",
    icon: Scissors,
    steps: [
      { id: "13-1", number: 1, name: "Codon Optimization", tool: "JCat" },
      { id: "13-2", number: 2, name: "Restriction Site Analysis", tool: "BioPython" },
      { id: "13-3", number: 3, name: "In Silico Cloning", tool: "Benchling" },
    ],
  },
  {
    number: 14,
    name: "Immune Simulation",
    icon: Activity,
    steps: [
      { id: "14-1", number: 1, name: "Immune Response Simulation", tool: "C-ImmSim" },
    ],
  },
];

export const TOTAL_STEPS = PIPELINE_PHASES.reduce((acc, p) => acc + p.steps.length, 0);

export const JOB_STATUS_META: Record<JobStatus, JobStatusMeta> = {
  created: { label: "Created", color: "#94A3B8", dot: "#94A3B8" },
  running: { label: "Running", color: "#2563EB", dot: "#3B82F6" },
  paused: { label: "Paused", color: "#F59E0B", dot: "#F59E0B" },
  completed: { label: "Completed", color: "#10B981", dot: "#10B981" },
  failed: { label: "Failed", color: "#EF4444", dot: "#EF4444" },
};

export const PHASE_STATUS_META: Record<PhaseStatus, { label: string; color: string }> = {
  pending: { label: "Pending", color: "#CBD5E1" },
  running: { label: "Running", color: "#3B82F6" },
  completed: { label: "Completed", color: "#10B981" },
  failed: { label: "Failed", color: "#EF4444" },
  paused: { label: "Paused", color: "#F59E0B" },
  skipped: { label: "Skipped", color: "#D1D5DB" },
};

export const STEP_STATUS_META: Record<StepStatus, { label: string; color: string }> = {
  pending: { label: "Pending", color: "#CBD5E1" },
  running: { label: "Running", color: "#3B82F6" },
  success: { label: "Success", color: "#10B981" },
  failed: { label: "Failed", color: "#EF4444" },
  skipped: { label: "Skipped", color: "#D1D5DB" },
  paused: { label: "Paused", color: "#F59E0B" },
};

export interface JobStatusMeta {
  label: string;
  color: string;
  dot: string;
}

export const ADJUVANTS = [
  { id: "ctxb", label: "Cholera enterotoxin subunit B (CTxB)", uniprot: "P01556", default: true },
  { id: "tlr4", label: "TLR4 agonist", uniprot: null },
  { id: "tlr5", label: "TLR5 agonist", uniprot: null },
  { id: "none", label: "No adjuvant", uniprot: null },
];

export const EXPRESSION_HOSTS = [
  { id: "ecoli", label: "E. coli K-12", default: true },
  { id: "cho", label: "CHO cells" },
  { id: "pichia", label: "Pichia pastoris" },
];

export const EXPRESSION_VECTORS = [
  { id: "pet28a", label: "pET-28a(+)" },
  { id: "pet22b", label: "pET-22b(+)" },
  { id: "pvax1", label: "pVAX1" },
  { id: "custom", label: "Custom" },
];

export const HLA_MHC1_COMMON = [
  "HLA-A*01:01",
  "HLA-A*02:01",
  "HLA-A*24:02",
  "HLA-A*31:01",
  "HLA-B*07:02",
  "HLA-B*35:01",
  "HLA-B*44:03",
  "HLA-B*57:01",
  "HLA-C*04:01",
  "HLA-C*07:01",
];

export const HLA_MHC2_COMMON = [
  "HLA-DRB1*03:01",
  "HLA-DRB1*04:01",
  "HLA-DRB1*07:01",
  "HLA-DRB1*11:01",
  "HLA-DRB1*15:01",
  "HLA-DQA1*05:01",
  "HLA-DPB1*04:01",
];

export const DEFAULT_HLA_MHC1 = ["HLA-A*02:01", "HLA-A*24:02", "HLA-A*31:01", "HLA-B*07:02", "HLA-B*35:01", "HLA-B*44:03", "HLA-B*57:01", "HLA-C*04:01", "HLA-C*07:01"];
export const DEFAULT_HLA_MHC2 = ["HLA-DRB1*03:01", "HLA-DRB1*04:01", "HLA-DRB1*07:01", "HLA-DRB1*11:01", "HLA-DRB1*15:01", "HLA-DQA1*05:01", "HLA-DPB1*04:01"];

export const COVERAGE_REGIONS = [
  "Global",
  "North America",
  "Central America",
  "South America",
  "Europe",
  "West Asia",
  "South Asia",
  "East Asia",
  "Southeast Asia",
  "Oceania",
  "North Africa",
  "Sub-Saharan Africa",
  "Northeast Asia",
];

export const COVERAGE_REGION_DEFAULT = [
  "Global",
  "North America",
  "Europe",
  "South Asia",
];

export const PATHOGEN_SUGGESTIONS = [
  { name: "Streptococcus agalactiae", strain: "2603 V/R", taxonId: 1169164 },
  { name: "Streptococcus agalactiae", strain: "NEM316", taxonId: 291485 },
  { name: "Mycobacterium tuberculosis", strain: "H37Rv", taxonId: 83332 },
  { name: "Escherichia coli", strain: "K-12 MG1655", taxonId: 511145 },
  { name: "Staphylococcus aureus", strain: "MRSA252", taxonId: 282458 },
  { name: "Pseudomonas aeruginosa", strain: "PAO1", taxonId: 208964 },
  { name: "Klebsiella pneumoniae", strain: "MGH 78578", taxonId: 573 },
  { name: "Neisseria meningitidis", strain: "MC58", taxonId: 122586 },
  { name: "Acinetobacter baumannii", strain: "ATCC 17978", taxonId: 400667 },
  { name: "Enterococcus faecium", strain: "Aus0004", taxonId: 1246586 },
];

export const TOOL_REGISTRY: ToolInfo[] = [
  { id: "uniprot", name: "UniProt REST API", phase: 1, usedFor: "Proteome retrieval", apiKind: "api", endpoint: "https://rest.uniprot.org", availability: "online" },
  { id: "cdhit", name: "CD-HIT", phase: 1, usedFor: "Redundancy removal", apiKind: "local", availability: "local" },
  { id: "blast", name: "NCBI BLAST", phase: 2, usedFor: "Essentiality / homology searches", apiKind: "api", endpoint: "https://blast.ncbi.nlm.nih.gov", availability: "rate_limited" },
  { id: "deg", name: "DEG Database", phase: 2, usedFor: "Essential gene identification", apiKind: "api", availability: "online" },
  { id: "psortb", name: "PSORTb 3.0", phase: 2, usedFor: "Subcellular localization", apiKind: "api", endpoint: "https://www.psort.org/psortb", availability: "online" },
  { id: "deeptmhmm", name: "DeepTMHMM", phase: 2, usedFor: "Transmembrane helix prediction", apiKind: "api", endpoint: "https://dtu.biolib.com/DeepTMHMM", availability: "online" },
  { id: "phobius", name: "Phobius", phase: 2, usedFor: "Signal peptide prediction", apiKind: "api", endpoint: "https://phobius.sbc.su.se", availability: "online" },
  { id: "algpred", name: "AlgPred 2.0", phase: 3, usedFor: "Allergenicity prediction", apiKind: "api", endpoint: "https://webs.iiitd.edu.in/raghava/algpred2", availability: "online" },
  { id: "vaxijen", name: "VaxiJen", phase: 3, usedFor: "Antigenicity prediction", apiKind: "api", endpoint: "http://www.ddg-pharmfac.net/vaxijen", availability: "online" },
  { id: "vfdb", name: "VFDB", phase: 3, usedFor: "Virulence factor database", apiKind: "api", availability: "online" },
  { id: "humanproteome", name: "Human Proteome (NCBI)", phase: 3, usedFor: "Human homology screening", apiKind: "api", availability: "online" },
  { id: "protparam", name: "ProtParam", phase: 4, usedFor: "Physicochemical properties", apiKind: "local", availability: "local" },
  { id: "swissmodel", name: "SwissModel", phase: 4, usedFor: "Homology 3D modelling", apiKind: "api", endpoint: "https://swissmodel.expasy.org/api", availability: "online" },
  { id: "alphafold", name: "AlphaFold", phase: 11, usedFor: "MEV 3D structure prediction", apiKind: "api", endpoint: "https://alphafold.ebi.ac.uk", availability: "online" },
  { id: "errat", name: "ERRAT", phase: 4, usedFor: "Structure quality validation", apiKind: "api", endpoint: "https://saves.mbi.ucla.edu", availability: "online" },
  { id: "sopma", name: "SOPMA", phase: 4, usedFor: "Secondary structure prediction", apiKind: "api", endpoint: "https://npsa-prabi.ibcp.fr", availability: "online" },
  { id: "iedb_mhci", name: "IEDB MHC-I (NetMHCpan)", phase: 5, usedFor: "CTL epitope prediction", apiKind: "api", endpoint: "https://tools.iedb.org/mhci", availability: "rate_limited" },
  { id: "toxinpred", name: "ToxinPred", phase: 5, usedFor: "Toxicity prediction", apiKind: "api", endpoint: "https://crdd.osdd.net/raghava/toxinpred", availability: "online" },
  { id: "iedb_immuno", name: "IEDB Immunogenicity", phase: 5, usedFor: "CTL immunogenicity scoring", apiKind: "api", endpoint: "https://tools.iedb.org/immunogenicity", availability: "rate_limited" },
  { id: "iedb_mhcii", name: "IEDB MHC-II (NetMHCIIpan)", phase: 6, usedFor: "HTL epitope prediction", apiKind: "api", endpoint: "https://tools.iedb.org/mhcii", availability: "rate_limited" },
  { id: "ifnepitope", name: "IFNepitope", phase: 6, usedFor: "IFN-γ induction", apiKind: "scrape", endpoint: "https://webs.iiitd.edu.in/raghava/ifnepitope", availability: "scrape" },
  { id: "il4pred", name: "IL4Pred", phase: 6, usedFor: "IL-4 induction", apiKind: "scrape", endpoint: "https://webs.iiitd.edu.in/raghava/il4pred", availability: "scrape" },
  { id: "il10pred", name: "IL10Pred", phase: 6, usedFor: "IL-10 induction", apiKind: "scrape", endpoint: "https://webs.iiitd.edu.in/raghava/il10pred", availability: "scrape" },
  { id: "abcpred", name: "ABCpred", phase: 7, usedFor: "Linear B-cell epitopes", apiKind: "scrape", endpoint: "https://webs.iiitd.edu.in/raghava/abcpred", availability: "scrape" },
  { id: "ellipro", name: "ElliPro", phase: 7, usedFor: "Conformational B-cell epitopes", apiKind: "scrape", endpoint: "https://tools.iedb.org/ellipro", availability: "scrape" },
  { id: "iedb_popcov", name: "IEDB Population Coverage", phase: 8, usedFor: "Population coverage analysis", apiKind: "api", endpoint: "https://tools.iedb.org/population", availability: "rate_limited" },
  { id: "biopython", name: "BioPython", phase: 9, usedFor: "Sequence assembly & analysis", apiKind: "local", availability: "local" },
  { id: "proteinsol", name: "Protein-Sol", phase: 10, usedFor: "Solubility prediction", apiKind: "api", endpoint: "https://protein-sol.manchester.ac.uk", availability: "online" },
  { id: "molprobity", name: "MolProbity", phase: 11, usedFor: "Ramachandran plot analysis", apiKind: "api", endpoint: "http://molprobity.biochem.duke.edu", availability: "online" },
  { id: "prosa", name: "ProSA-web", phase: 11, usedFor: "Structure Z-score validation", apiKind: "api", endpoint: "https://prosa.services.came.sbg.ac.at", availability: "online" },
  { id: "dbd2", name: "Disulfide by Design 2", phase: 12, usedFor: "Disulfide bond engineering", apiKind: "scrape", endpoint: "http://disulfide.bii.a-star.edu.sg", availability: "scrape" },
  { id: "jcat", name: "JCat", phase: 13, usedFor: "Codon optimization", apiKind: "api", endpoint: "https://www.jcat.de", availability: "online" },
  { id: "benchling", name: "Benchling", phase: 13, usedFor: "In silico cloning", apiKind: "api", endpoint: "https://api.benchling.com", availability: "online" },
  { id: "cimmsim", name: "C-ImmSim", phase: 14, usedFor: "Immune response simulation", apiKind: "scrape", endpoint: "https://kraken.ijs.si/data/cimmsim", availability: "scrape" },
];
