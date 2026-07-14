# GPU Cloud Provider Comparison — Sawyer TIER_4 Seed Node

**Date**: July 2026  
**Purpose**: Select cloud GPU hosting for the first Sawyer seed node (TIER_4, 24GB+ VRAM)  
**Router**: api.sawyer.infill.systems on Hetzner (5.78.214.237)  
**Requirement**: NVIDIA GPU with 24GB+ VRAM, CUDA 12+, persistent ~30GB storage, gRPC connectivity to router

---

## Quick Summary

| Provider | Best 24GB GPU | On-Demand $/hr | Spot/Interrupt $/hr | Est Monthly (24/7) | Recommendation |
|----------|---------------|----------------|----------------------|---------------------|----------------|
| **Vast.ai** | RTX 3090 | ~$0.13 | ~$0.07 | **$94** (on-demand) / **$51** (interrupt) | ⭐ **BEST for seed node** |
| **RunPod (Community)** | RTX 3090 | $0.22 | N/A (community) | $161 | Good backup, familiar tooling |
| **RunPod (Secure)** | L4 / RTX 3090 | $0.39–0.46 | N/A | $283–335 | Expensive for 24/7 use |
| **Lambda Labs** | RTX 3090 / A100 | ~$0.50 | ~$0.25 | ~$365 | Overpriced, enterprise-focused |
| **CoreWeave** | L4 only (8x min) | N/A | N/A | N/A | ❌ No single-GPU offering |

**Recommendation**: **Vast.ai with an RTX 3090 on interruptible pricing** for the initial seed node. Cheapest option at ~$51/mo, 120+ RTX 3090 instances available, and the Sawyer seed node is fault-tolerant (can reconnect if preempted). For production stability, use on-demand at ~$94/mo.

---

## Detailed Provider Comparison

### 1. Vast.ai — Community GPU Marketplace

**Best fit for Sawyer seed node.** Lowest prices, widest GPU selection, interruptible instances are 50%+ cheaper.

| Aspect | Details |
|--------|---------|
| **RTX 3090 (24GB)** | On-demand: ~$0.13/hr, Interruptible: ~$0.07/hr. **High availability** (120+ instances) |
| **RTX 4090 (24GB)** | On-demand: ~$0.34/hr ($246/mo), Interruptible: ~$0.13/hr ($94/mo). High availability |
| **A100 SXM4 (80GB)** | On-demand: ~$0.77/hr ($558/mo). Medium availability |
| **RTX A6000 (48GB)** | On-demand: ~$0.39/hr ($283/mo). Low availability |
| **L4 (24GB)** | On-demand: ~$0.32/hr ($232/mo). Low availability |
| **RTX PRO 4000 (24GB)** | On-demand: ~$0.21/hr ($152/mo). **Low availability** |
| **Monthly cost (24/7, cheapest 24GB)** | **RTX 3090 interruptible: ~$51/mo** |
| **Monthly cost (24/7, cheapest 24GB on-demand)** | **RTX 3090 on-demand: ~$94/mo** |
| **Spot/Interruptible** | Yes, "interruptible" tier. 50%+ discount. Can be reclaimed at any time. |
| **Storage** | Persistent storage available. Instance storage: $0.15/GB/mo for container disk, $0.05–0.07/GB/mo for network storage |
| **30GB storage cost** | ~$1.50–2.10/mo |
| **Network bandwidth** | Ingress free, egress ~$0.01–0.05/GB. Typical 1Gbps+ connectivity |
| **CUDA support** | Host-provided Docker images with CUDA 12.x. Can bring custom images. |
| **Uptime/SLA** | ❌ **No SLA.** Community hosts can go offline anytime. Interruptible instances can be reclaimed. |
| **Long-running services** | ✅ On-demand instances can run indefinitely. Interruptible instances risk preemption. |
| **Setup** | Docker-based. SSH access. Can use custom Docker images with llama.cpp. CLI and web console. |
| **Geographic** | 40+ data centers, mostly US. Good US coverage for low latency to Hetzner US East. |
| **Key advantage** | Cheapest GPU hours on the market. RTX 3090 at $0.13/hr is unmatched. |
| **Key risk** | No SLA. Community hosts may have inconsistent reliability. Interruptible = can be killed anytime. |

### 2. RunPod — GPU Cloud with Spot and On-Demand

**Good backup option.** Already used for Sawyer builds (RunPod build docs exist). Familiar tooling.

| Aspect | Details |
|--------|---------|
| **RTX 3090 (24GB)** | Community: $0.22/hr, Secure: $0.46/hr |
| **RTX 4090 (24GB)** | Community: $0.34/hr, Secure: $0.69/hr |
| **A100 PCIe (80GB)** | Community: $1.19/hr, Secure: $1.39/hr |
| **A100 SXM (80GB)** | Community: $1.39/hr, Secure: $1.49/hr |
| **L4 (24GB)** | Community: $0.44/hr, Secure: $0.39/hr |
| **RTX A6000 (48GB)** | Community: $0.33/hr, Secure: $0.49/hr |
| **Monthly cost (24/7, cheapest 24GB)** | **Community RTX 3090: $161/mo** |
| **Monthly cost (24/7, Secure cheapest 24GB)** | **Secure L4: $283/mo** |
| **Spot/Preemptible** | Community instances can be preempted. Secure Cloud has better reliability. |
| **Storage** | Container disk: $0.10/GB/mo. Network volume (running): $0.10/GB/mo. Network volume (idle): $0.20/GB/mo. Standard network storage: $0.05–0.07/GB/mo (over 1TB). High-performance: $0.14/GB/mo |
| **30GB storage cost** | ~$1.50–3.00/mo (container or volume) |
| **Network bandwidth** | Ingress free. Egress varies by plan. Generally 1Gbps+ for Secure Cloud. |
| **CUDA support** | ✅ Full CUDA 12.x support. Pre-built PyTorch/CUDA images. Custom Docker support. |
| **Uptime/SLA** | Secure Cloud has better reliability. Community Cloud: no formal SLA. |
| **Long-running services** | ✅ Pods are designed for long-running workloads. Persistent storage survives pod stops. |
| **Setup** | Docker-based. SSH access. Jupyter terminal. Custom templates. **Already have RunPod build docs.** |
| **Geographic** | 30+ regions. US regions available. Good connectivity to Hetzner. |
| **Key advantage** | Already familiar with RunPod (build docs exist). Jupyter terminal works when SSH fails. |
| **Key risk** | Community instances ~2x more expensive than Vast.ai for same GPU. Secure Cloud even more. |

### 3. Lambda Labs — GPU Cloud for ML Workloads

**Not recommended.** Lambda has pivoted to "AI Factories" (superclusters). Their cloud GPU instances product (formerly lambdalabs.com) appears to be deprecated/redirected. Pricing from historical data and web archives.

| Aspect | Details |
|--------|---------|
| **RTX 3090 (24GB)** | Historical: ~$0.50/hr ($365/mo). May no longer be offered. |
| **RTX 4090 (24GB)** | Historical: ~$0.70/hr ($511/mo). May no longer be offered. |
| **A100 (40GB)** | Historical: ~$1.10/hr ($800/mo). |
| **A100 (80GB)** | Historical: ~$1.55/hr ($1,131/mo). |
| **Monthly cost (24/7, cheapest 24GB)** | **~$365/mo** (if still available) |
| **Spot/Preemptible** | Historical: Yes, ~50% discount on some GPUs. |
| **Storage** | Included with instance. Persistent storage available. |
| **Network bandwidth** | Generally good. |
| **CUDA support** | ✅ Full CUDA support. Lambda Stack (Ubuntu-based). |
| **Uptime/SLA** | No formal SLA on instance cloud. |
| **Long-running services** | ✅ Yes, but instances can be preempted if demand is high. |
| **Setup** | SSH access. Bare-metal-like. Ubuntu + Lambda Stack. Good for custom installs. |
| **Geographic** | US regions (Texas, California, etc.). Good latency to Hetzner US East. |
| **Key advantage** | Simple setup. Lambda Stack has CUDA pre-installed. |
| **Key risk** | ❌ **Product appears discontinued.** Website now redirects to "AI Factories" (superclusters). No longer offering single-GPU cloud instances. |

### 4. CoreWeave — Enterprise GPU Cloud

**Not suitable for a seed node.** CoreWeave is enterprise-focused with minimum 8-GPU instances. No single-GPU options at reasonable prices.

| Aspect | Details |
|--------|---------|
| **Minimum purchase** | 8-GPU instances (H100, A100, L40S, etc.) |
| **A100 8x80GB** | On-demand: $21.60/hr ($15,768/mo). Spot: $9.65/hr ($7,034/mo). |
| **L40S 8x48GB** | On-demand: $18.00/hr ($13,104/mo). Spot: $7.88/hr ($5,746/mo). |
| **L4** | Not listed as single-GPU. Only in multi-GPU configs. |
| **Inference pricing** | A100 8x inference: $2.70/hr ($1,970/mo). Still 8-GPU minimum. |
| **Monthly cost (minimum)** | **$5,746/mo (8x L40S spot)** — absurdly expensive for a seed node |
| **Spot/Preemptible** | Yes, ~50% discount. Still requires 8-GPU minimum. |
| **Storage** | Enterprise-grade. Various tiers. Pricing not publicly listed for small amounts. |
| **Network bandwidth** | Enterprise-grade. Egress pricing varies. |
| **CUDA support** | ✅ Full CUDA 12.x support. Managed infrastructure. |
| **Uptime/SLA** | ✅ Enterprise SLAs available. 99.9%+ uptime. |
| **Long-running services** | ✅ Designed for production inference workloads. |
| **Setup** | Kubernetes-based (CoreWeave Kubernetes Service). Complex setup. Not for hobbyist/single-node use. |
| **Geographic** | US (multiple regions) + Europe. Excellent connectivity. |
| **Key advantage** | Enterprise-grade reliability and SLAs. |
| **Key risk** | ❌ **8-GPU minimum purchase.** Cost is 10-100x more than alternatives. Overkill for a seed node. |

---

## Cost Comparison Table (24/7 Monthly, Cheapest 24GB+ GPU per Provider)

| Provider | GPU | Tier | $/hr | Monthly (24/7) | 30GB Storage | Total Monthly | Notes |
|----------|-----|------|------|-----------------|---------------|---------------|-------|
| **Vast.ai** | RTX 3090 | Interruptible | $0.07 | **$51** | $1.50 | **$52.50** | ⭐ Cheapest. Can be preempted. |
| **Vast.ai** | RTX 3090 | On-demand | $0.13 | $94 | $1.50 | **$95.50** | Best reliability/price ratio |
| **Vast.ai** | RTX PRO 4000 | On-demand | $0.21 | $152 | $1.50 | $153.50 | Blackwell gen, 24GB |
| **Vast.ai** | RTX 4090 | Interruptible | $0.13 | $94 | $1.50 | $95.50 | Faster than 3090 |
| **Vast.ai** | RTX 4090 | On-demand | $0.34 | $246 | $1.50 | $247.50 | |
| RunPod Comm | RTX 3090 | On-demand | $0.22 | $161 | $3.00 | $164 | Familiar tooling |
| RunPod Comm | RTX A5000 | On-demand | $0.16 | $117 | $3.00 | $120 | 24GB, server-grade |
| RunPod Comm | RTX 4090 | On-demand | $0.34 | $246 | $3.00 | $249 | |
| RunPod Secure | L4 | On-demand | $0.39 | $283 | $3.00 | $286 | Server-grade, good SLA |
| RunPod Secure | RTX 3090 | On-demand | $0.46 | $335 | $3.00 | $338 | |
| Lambda Labs | RTX 3090 | On-demand | ~$0.50 | ~$365 | ~included | ~$365 | ⚠️ Product may be discontinued |
| CoreWeave | (8x minimum) | Spot | $7.88 | $5,746 | included | $5,746 | ❌ 8-GPU minimum |

---

## Break-Even Analysis vs Owned Hardware

From the Sawyer skill reference: **Owned hardware is the sustainable path.** Cloud GPU pricing is a losing proposition for subscription services.

| Option | Upfront | Monthly | Break-even | Annual Cost |
|--------|---------|---------|------------|-------------|
| **Used RTX 3090 (owned)** | $700 | ~$30 (power) | N/A | $360 |
| **Vast.ai RTX 3090 (interruptible)** | $0 | $52.50 | Never | $630 |
| **Vast.ai RTX 3090 (on-demand)** | $0 | $95.50 | 7.3 months | $1,146 |
| **RunPod Community RTX 3090** | $0 | $164 | Never | $1,968 |
| **RunPod Secure L4** | $0 | $286 | Never | $3,432 |

**Owned RTX 3090 pays for itself in 7 months vs Vast.ai on-demand, or 13 months vs Vast.ai interruptible.** But for a seed node (needed now, for proof-of-concept), cloud is the right choice until the network has paying subscribers.

---

## Recommendation: Vast.ai RTX 3090 (On-Demand)

### Why Vast.ai?

1. **Lowest cost**: RTX 3090 on-demand at $0.13/hr ($95.50/mo with storage) is 40% cheaper than RunPod Community ($164/mo) and 70% cheaper than RunPod Secure ($286/mo).

2. **High availability**: 120+ RTX 3090 instances available. The "High" availability rating means instances are easy to find.

3. **gRPC-friendly**: Docker-based with SSH access. Can install llama.cpp with CUDA 12.x, configure `sawyer serve`, and expose gRPC port.

4. **US data centers**: Good latency to Hetzner US East (Ashburn, VA). The Sawyer router is at 5.78.214.237 (Hetzner Finland), so cross-Atlantic latency is ~80-100ms — acceptable for MoE expert serving where each request may involve multiple small inference calls.

5. **Persistent storage**: Can persist model weights across instance restarts.

6. **Interruptible option**: If budget is tight, interruptible at $0.07/hr ($52.50/mo) is even cheaper. Sawyer's `sawyer serve` is designed to reconnect to the router — brief interruptions are tolerable for a seed node.

### Setup Plan

1. **Create Vast.ai account** at console.vast.ai
2. **Search for RTX 3090 instances** with ≥24GB VRAM, CUDA 12+, ≥30GB disk
3. **Select on-demand** (not interruptible for initial seed node stability)
4. **Use Docker image** with CUDA 12.x runtime + llama.cpp pre-built
5. **Install sawyer-core**: `pip install sawyer-core`
6. **Download expert weights** (~30GB, one-time)
7. **Run**: `sawyer serve --router api.sawyer.infill.systems:8001`
8. **Configure auto-restart** on instance reboot

### Risk Mitigation

- **No SLA**: Keep a RunPod Community RTX 3090 as a failover. RunPod has familiar tooling and can be spun up in minutes.
- **Preemption on interruptible**: Use on-demand tier for the seed node. The $0.06/hr premium ($43/mo) buys guaranteed uptime.
- **Long-term**: Once the network has paying subscribers, transition to owned hardware (used RTX 3090 ~$700, pays for itself in 7 months).

### Lambda Labs and CoreWeave Verdicts

- **Lambda Labs**: Their cloud instances product appears discontinued in favor of "AI Factories" (superclusters). Not viable for a single-GPU seed node.
- **CoreWeave**: 8-GPU minimum at $5,746+/mo. Designed for enterprise inference clusters, not single-node seeding. Not relevant for our use case.

---

## Appendix: Raw Pricing Data (July 2026)

### Vast.ai Live Prices (On-Demand / Interruptible Range)

| GPU | VRAM | On-Demand $/hr | Range | Monthly (24/7) |
|-----|------|---------------|-------|----------------|
| RTX 3060 | 12GB | $0.05 | $0.03–1.33 | $36 |
| RTX 3090 | 24GB | $0.13 | $0.07–1.33 | $94 |
| RTX 3090 TI | 24GB | $0.17 | $0.11–0.29 | $123 |
| RTX A5000 | 24GB | $0.20 | $0.07–0.47 | $145 |
| RTX PRO 4000 | 24GB | $0.21 | $0.15–0.33 | $152 |
| L4 | 24GB | $0.32 | $0.03–0.56 | $232 |
| RTX 4090 | 24GB | $0.34 | $0.13–6.67 | $246 |
| A40 | 48GB | $0.29 | $0.28–0.60 | $210 |
| RTX A6000 | 48GB | $0.39 | $0.27–0.67 | $283 |
| L40S | 48GB | $0.47 | $0.40–2.44 | $341 |
| A100 PCIE | 80GB | $0.53 | $0.27–1.80 | $385 |
| A100 SXM4 | 80GB | $0.77 | $0.40–1.57 | $558 |

### RunPod Community Cloud Prices (Per Hour)

| GPU | VRAM | $/hr | Monthly (24/7) |
|-----|------|------|----------------|
| RTX A5000 | 24GB | $0.16 | $117 |
| RTX 3090 | 24GB | $0.22 | $161 |
| L4 | 24GB | $0.44 | $322 |
| RTX 4090 | 24GB | $0.34 | $246 |
| RTX A6000 | 48GB | $0.33 | $241 |
| A40 | 48GB | $0.35 | $255 |
| A100 PCIe | 80GB | $1.19 | $867 |
| A100 SXM | 80GB | $1.39 | $1,013 |

### RunPod Secure Cloud Prices (Per Hour)

| GPU | VRAM | $/hr | Monthly (24/7) |
|-----|------|------|----------------|
| RTX A5000 | 24GB | $0.27 | $197 |
| RTX 3090 | 24GB | $0.46 | $335 |
| L4 | 24GB | $0.39 | $283 |
| RTX 4090 | 24GB | $0.69 | $503 |
| RTX A6000 | 48GB | $0.49 | $357 |
| A40 | 48GB | $0.44 | $321 |
| A100 PCIe | 80GB | $1.39 | $1,013 |

### CoreWeave Prices (8-GPU Instances Only)

| GPU Config | VRAM/GPU | On-demand $/hr | Spot $/hr | Inference $/hr |
|------------|-----------|---------------|-----------|----------------|
| A100 8x | 80GB | $21.60 | $9.65 | $2.70 |
| L40S 8x | 48GB | $18.00 | $7.88 | $2.25 |
| H100 8x | 80GB | $49.24 | $19.71 | $6.16 |

### Vast.ai Storage Pricing

| Type | Cost |
|------|------|
| Container disk | $0.15/GB/mo |
| Network storage (standard, <1TB) | $0.07/GB/mo |
| Network storage (standard, >1TB) | $0.05/GB/mo |
| Network storage (high-performance) | $0.14/GB/mo |

### RunPod Storage Pricing

| Type | Cost |
|------|------|
| Container disk | $0.10/GB/mo |
| Volume disk (running) | $0.10/GB/mo |
| Volume disk (idle) | $0.20/GB/mo |
| Network storage (standard, <1TB) | $0.07/GB/mo |
| Network storage (standard, >1TB) | $0.05/GB/mo |
| Network storage (high-performance) | $0.14/GB/mo |

### RunPod Serverless Pricing (Alternative Approach)

For comparison — RunPod Serverless could host Sawyer experts as API endpoints:

| GPU Class | $/hr |
|-----------|------|
| 24GB (4090) | $1.10/hr |
| 24GB (L4/A5000/3090) | $0.69/hr |
| 48GB (A6000/A40) | $1.22/hr |
| 80GB (A100) | $2.72/hr |

Serverless pricing is per-request (billed per second of GPU time), which could be cheaper if Sawyer has low traffic. But it's not suitable for a seed node that needs to be always-online for gRPC connections.

---

*Generated: July 11, 2026. Prices are live market rates and may fluctuate. Vast.ai prices are supply/demand-driven and change frequently. RunPod prices are fixed published rates.*