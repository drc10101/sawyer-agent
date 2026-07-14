# Sawyer Provider Recruitment Posts

Community posts for recruiting GPU owners to the Sawyer distributed MoE inference network.

---

## r/LocalLLaMA

**Title:** Your idle RTX 3090 could be earning money right now -- Sawyer is recruiting GPU hosts

I built Sawyer because I watched my RTX 3090 sit idle 95% of the day while I paid OpenAI for inference. That's backwards.

Sawyer distributes Mixture-of-Experts models across consumer GPUs. When a prompt comes in, the router activates only the 2-6 relevant experts, and each expert runs on a separate node. Your GPU holds 1-3 expert weight files (about 1.5GB each for Mixtral) and serves inference requests in the background. You can still game or work while it runs.

**The deal:**
- 70% of every token you serve goes to you
- Payouts via Stripe Connect, monthly at $10 minimum or quarterly at $25
- 1099-K tax reporting handled automatically
- One command: `sawyer register && sawyer serve --gpu`

**Requirements:**
- NVIDIA GPU with 6GB+ VRAM and CUDA 12+
- 5 Mbps+ upload
- ~3GB disk per expert

We're in early access. The router is live, the benchmarking tool shows 64.5% prefill speedup over vanilla llama.cpp, and we need nodes to build out the network.

The code is open source: https://github.com/drc10101/sawyer

Happy to answer questions.

---

## r/selfhosted

**Title:** Self-hosting an inference node that pays for itself -- Sawyer is looking for GPU hosts

If you're already self-hosting services, you probably have a GPU that's mostly idle. Sawyer lets you put it to work.

Sawyer is a distributed MoE inference network. Your GPU hosts individual expert weight files (~1.5GB each) and serves forward passes when prompts come in. It runs in the background -- no Docker, no Kubernetes, no manual config. Just `sawyer serve --gpu`.

**Why this is different from crypto mining:**
- No wasted compute. You serve real inference requests.
- No volatile token economics. You earn 70% of what users pay, in USD.
- No ASIC arms race. Your RTX 3060 is useful, not obsolete.
- No complicated setup. Two commands and you're running.

**What you need:**
- NVIDIA GPU, 6GB+ VRAM, CUDA 12+
- Stable internet (5 Mbps+ upload)
- About 3GB disk per expert

Payouts via Stripe Connect, monthly or quarterly. Tax reporting included.

Open source: https://github.com/drc10101/sawyer

Ask me anything.

---

## r/SideProject

**Title:** I built a distributed inference network that pays you to run it on your GPU

Sawyer splits Mixture-of-Experts language models across consumer GPUs. Each node holds a few expert weight files and serves inference requests in the background. It's like SETI@home but you get paid.

70% revenue share. Monthly Stripe payouts. One command to start.

The interesting technical challenge: MoE models only activate 2-6 experts per token out of 8-64 total. So the work distributes naturally -- no tensor parallelism, no synchronized communication between nodes, no bottleneck. Each expert is independent.

We benchmarked 64.5% prefill speedup over vanilla llama.cpp with our optimized binary.

If you have an idle NVIDIA GPU (6GB+ VRAM) and want to earn from it: https://github.com/drc10101/sawyer

---

## Discord: LocalLLaMA / AI / GPU servers

Short version for Discord:

Got an idle RTX 3090/4090? Sawyer (open source distributed MoE inference) is recruiting GPU hosts. 70% revenue share, Stripe payouts, one command to start. Your GPU holds 1-3 expert files (~1.5GB each) and serves inference in the background. No Docker, no K8s. Just `sawyer register && sawyer serve --gpu`.

Details: https://github.com/drc10101/sawyer

---

## Posting Guidelines

- Post on r/LocalLLaMA first (most targeted audience)
- Wait 24-48 hours, then post r/selfhosted
- r/SideProject can go same day as r/selfhosted
- Discord posts go after Reddit posts gain traction
- Respond to every comment within 2 hours during the first 24 hours
- Do NOT mention the kanban or that these are templated posts
- Link to the landing page (sawyer.infill.systems) for the general audience
- Link to GitHub (github.com/drc10101/sawyer) for the technical audience
- When asked about earnings, reference the estimates: RTX 3060 ~$15-50/mo, RTX 3090 ~$40-120/mo, RTX 4090 ~$60-180/mo