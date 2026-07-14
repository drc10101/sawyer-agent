"""
Sawyer - Faceless YouTube Video
"You're Paying 10x Too Much for AI Inference"

Original concept: Visual demonstration of the price gap between
big-provider AI APIs and distributed inference, showing Sawyer
as the marketplace that closes it.

Style: Neon tech on black, clean data visualization, no narration needed.
"""

from manim import *

BG = "#0A0A0A"
PRIMARY = "#00F5FF"    # Cyan - Sawyer brand
SECONDARY = "#FF00FF"  # Magenta - cost/pain
ACCENT = "#39FF14"     # Neon green - savings
WARN = "#FF4444"       # Red - overpayment
WHITE = "#FFFFFF"
DIM = "#555555"
GOLD = "#FFD700"
MONO = "Menlo"


class Scene1_PriceShock(Scene):
    """Open with the brutal price comparison"""
    def construct(self):
        self.camera.background_color = BG
        
        # Three prices appear one at a time
        prices = [
            ("OpenAI GPT-4o", "$5.00", "1M tokens"),
            ("Anthropic Claude", "$3.00", "1M tokens"),
            ("Sawyer Network", "$0.15", "1M tokens"),
        ]
        
        self.add_subcaption("AI inference doesn't have to cost this much.", duration=3)
        
        group = VGroup()
        y_pos = 2.0
        for i, (name, price, unit) in enumerate(prices):
            color = WARN if i < 2 else ACCENT
            n = Text(name, font_size=22, color=DIM, font=MONO)
            p = Text(price, font_size=48, color=color, font=MONO, weight=BOLD)
            u = Text(f"/ {unit}", font_size=18, color=DIM, font=MONO)
            row = VGroup(n, p, u).arrange(RIGHT, buff=0.2)
            row.move_to(UP * (2.0 - i * 1.2))
            group.add(row)
            
            self.play(FadeIn(row, shift=UP * 0.3), run_time=0.6)
            if i == 1:
                self.wait(0.5)
        
        self.wait(0.5)
        
        # The underline on Sawyer's price
        highlight = Underline(group[2][1], color=ACCENT, buff=0.1)
        self.play(Create(highlight), run_time=0.5)
        
        self.add_subcaption("Thirty-three times cheaper.", duration=2)
        self.wait(1.5)
        self.play(FadeOut(Group(group, highlight)), run_time=0.5)


class Scene2_TheGap(Scene):
    """Visual bar chart showing the price gap"""
    def construct(self):
        self.camera.background_color = BG
        
        title = Text("The Price Gap Is Real", font_size=42, color=PRIMARY, font=MONO, weight=BOLD)
        title.to_edge(UP, buff=0.6)
        self.play(Write(title), run_time=1.0)
        
        # Bar chart - horizontal bars
        providers = [
            ("OpenAI GPT-4o", 5.00, WARN),
            ("Anthropic Claude", 3.00, "#FF6B6B"),
            ("Google Gemini", 1.25, "#FFA500"),
            ("Sawyer Network", 0.15, ACCENT),
        ]
        
        max_price = 5.00
        bar_height = 0.5
        start_x = -5.0
        
        self.add_subcaption("Same models. Same quality. Fraction of the cost.", duration=3)
        
        bars = VGroup()
        labels = VGroup()
        for i, (name, price, color) in enumerate(providers):
            bar_width = (price / max_price) * 8.0
            
            bar = Rectangle(
                width=bar_width, height=bar_height,
                fill_color=color, fill_opacity=0.9,
                stroke_width=0,
            )
            bar.move_to(DOWN * (1.0 - i * 1.0) + RIGHT * (start_x + bar_width / 2 + 5.0))
            
            label = Text(name, font_size=20, color=WHITE if i < 3 else ACCENT, font=MONO)
            label.next_to(bar, LEFT, buff=0.2)
            
            price_label = Text(f"${price:.2f}", font_size=22, color=WHITE, font=MONO, weight=BOLD)
            price_label.next_to(bar, RIGHT, buff=0.2)
            
            bars.add(bar)
            labels.add(VGroup(label, price_label))
            
            self.play(GrowFromEdge(bar, edge=LEFT), FadeIn(label), run_time=0.5)
            self.play(Write(price_label), run_time=0.2)
        
        self.wait(1.5)
        
        # Arrow showing the gap
        arrow = Arrow(
            start=DOWN * 1.0 + RIGHT * 3.0,
            end=DOWN * 1.0 + RIGHT * 0.5,
            color=GOLD, stroke_width=4, max_tip_length_to_length_ratio=0.15,
        )
        savings = Text("33x cheaper", font_size=28, color=GOLD, font=MONO, weight=BOLD)
        savings.next_to(arrow, UP, buff=0.2)
        
        self.play(Create(arrow), run_time=0.5)
        self.play(Write(savings), run_time=0.5)
        self.wait(2.0)
        self.play(FadeOut(Group(title, bars, labels, arrow, savings)), run_time=0.5)


class Scene3_HowItWorks(Scene):
    """How Sawyer works - the marketplace"""
    def construct(self):
        self.camera.background_color = BG
        
        title = Text("How Sawyer Works", font_size=42, color=PRIMARY, font=MONO, weight=BOLD)
        title.to_edge(UP, buff=0.6)
        self.play(Write(title), run_time=1.0)
        
        # Left: GPU owner
        gpu_owner = VGroup(
            Text("GPU Owner", font_size=24, color=SECONDARY, font=MONO, weight=BOLD),
            Text("Idle hardware?", font_size=18, color=DIM, font=MONO),
            Text("Earn money.", font_size=18, color=ACCENT, font=MONO),
        ).arrange(DOWN, buff=0.2)
        gpu_owner.move_to(LEFT * 4 + UP * 0.5)
        
        # Center: Sawyer router
        router = VGroup(
            Text("Sawyer", font_size=32, color=PRIMARY, font=MONO, weight=BOLD),
            Text("Router", font_size=24, color=PRIMARY, font=MONO),
        ).arrange(DOWN, buff=0.1)
        router.move_to(ORIGIN + UP * 0.5)
        
        # Right: Developer
        developer = VGroup(
            Text("Developer", font_size=24, color=GOLD, font=MONO, weight=BOLD),
            Text("Need inference?", font_size=18, color=DIM, font=MONO),
            Text("Pay less.", font_size=18, color=ACCENT, font=MONO),
        ).arrange(DOWN, buff=0.2)
        developer.move_to(RIGHT * 4 + UP * 0.5)
        
        self.add_subcaption("People with idle GPUs earn money. Developers get cheaper inference. Sawyer routes the traffic.", duration=4)
        
        self.play(FadeIn(gpu_owner, shift=RIGHT * 0.5), run_time=0.6)
        self.play(Write(router), run_time=0.8)
        self.play(FadeIn(developer, shift=LEFT * 0.5), run_time=0.6)
        
        # Arrows connecting them
        arrow_left = Arrow(
            start=gpu_owner.get_right() + RIGHT * 0.3,
            end=router.get_left() + LEFT * 0.3,
            color=SECONDARY, stroke_width=3,
        )
        arrow_right = Arrow(
            start=router.get_right() + RIGHT * 0.3,
            end=developer.get_left() + LEFT * 0.3,
            color=GOLD, stroke_width=3,
        )
        
        self.play(Create(arrow_left), Create(arrow_right), run_time=0.6)
        
        # The split
        split = VGroup(
            Text("Provider: 70%", font_size=20, color=SECONDARY, font=MONO),
            Text("Platform: 30%", font_size=20, color=PRIMARY, font=MONO),
        ).arrange(RIGHT, buff=1.0)
        split.move_to(DOWN * 2.5)
        
        self.play(FadeIn(split), run_time=0.5)
        self.wait(2.0)
        self.play(FadeOut(Group(title, gpu_owner, router, developer, arrow_left, arrow_right, split)), run_time=0.5)


class Scene4_WhatYouGet(Scene):
    """What you get with Sawyer"""
    def construct(self):
        self.camera.background_color = BG
        
        title = Text("What You Get", font_size=42, color=PRIMARY, font=MONO, weight=BOLD)
        title.to_edge(UP, buff=0.6)
        self.play(Write(title), run_time=1.0)
        
        features = [
            ("OpenAI-compatible API", ACCENT, "Drop-in replacement. Same endpoints."),
            ("Multiple models", PRIMARY, "Llama, Mistral, Qwen, and more."),
            ("No rate limits", SECONDARY, "Distributed means no bottlenecks."),
            ("Pay per token", GOLD, "No minimums. No commitments."),
            ("Self-host or join", ACCENT, "Your hardware, your rules."),
        ]
        
        self.add_subcaption("OpenAI-compatible API. Same endpoints. Same models. Fraction of the cost.", duration=4)
        
        group = VGroup()
        for i, (feature, color, desc) in enumerate(features):
            check = Text("✓", font_size=24, color=color, font=MONO)
            name = Text(feature, font_size=26, color=WHITE, font=MONO, weight=BOLD)
            d = Text(f"  {desc}", font_size=18, color=DIM, font=MONO)
            row = VGroup(check, name, d).arrange(RIGHT, buff=0.15)
            row.move_to(UP * (1.8 - i * 0.75))
            group.add(row)
            
            self.play(FadeIn(row, shift=RIGHT * 0.3), run_time=0.35)
        
        self.wait(2.0)
        self.play(FadeOut(Group(title, group)), run_time=0.5)


class Scene5_Demo(Scene):
    """API demo - drop-in replacement"""
    def construct(self):
        self.camera.background_color = "#0D1117"
        
        # Show the code change
        self.add_subcaption("One line change. Same API. Different price.", duration=3)
        
        # Before
        before_title = Text("# Before", font_size=20, color=WARN, font=MONO)
        before_title.move_to(UP * 2.5)
        
        before_code = VGroup(
            Text('from openai import OpenAI', font_size=18, color=DIM, font=MONO),
            Text('', font_size=10, color=DIM, font=MONO),
            Text('client = OpenAI(', font_size=18, color=WHITE, font=MONO),
            Text('    api_key="sk-...",', font_size=18, color=DIM, font=MONO),
            Text('    base_url="https://api.openai.com/v1"', font_size=18, color=WARN, font=MONO),
            Text(')', font_size=18, color=WHITE, font=MONO),
        ).arrange(DOWN, aligned_edge=LEFT, buff=0.05)
        before_code.move_to(UP * 0.8)
        
        self.play(Write(before_title), run_time=0.5)
        for line in before_code:
            self.play(FadeIn(line, shift=RIGHT * 0.2), run_time=0.15)
        
        self.wait(0.5)
        
        # After
        after_title = Text("# After", font_size=20, color=ACCENT, font=MONO)
        after_title.move_to(DOWN * 0.6)
        
        after_code = VGroup(
            Text('from openai import OpenAI', font_size=18, color=DIM, font=MONO),
            Text('', font_size=10, color=DIM, font=MONO),
            Text('client = OpenAI(', font_size=18, color=WHITE, font=MONO),
            Text('    api_key="sawyer_...",', font_size=18, color=DIM, font=MONO),
            Text('    base_url="https://api.sawyer.infill.systems/v1"', font_size=18, color=ACCENT, font=MONO),
            Text(')', font_size=18, color=WHITE, font=MONO),
        ).arrange(DOWN, aligned_edge=LEFT, buff=0.05)
        after_code.move_to(DOWN * 2.3)
        
        self.add_subcaption("Change the base URL and API key. That's it.", duration=3)
        self.play(Write(after_title), run_time=0.5)
        for line in after_code:
            self.play(FadeIn(line, shift=RIGHT * 0.2), run_time=0.15)
        
        # Highlight the changed line
        self.wait(0.5)
        
        # Savings callout
        callout = Text("Same code. 33x cheaper.", font_size=24, color=GOLD, font=MONO, weight=BOLD)
        callout.move_to(DOWN * 4.0)
        self.play(Write(callout), run_time=0.8)
        
        self.wait(2.0)
        self.play(FadeOut(Group(before_title, before_code, after_title, after_code, callout)), run_time=0.5)


class Scene6_Tiers(Scene):
    """Pricing tiers"""
    def construct(self):
        self.camera.background_color = BG
        
        title = Text("Simple Pricing", font_size=42, color=PRIMARY, font=MONO, weight=BOLD)
        title.to_edge(UP, buff=0.6)
        self.play(Write(title), run_time=1.0)
        
        tiers = [
            ("Explorer", "$5", "mo", "For tinkering", DIM),
            ("Pro", "$15", "mo", "For building", PRIMARY),
            ("Pioneer", "$40", "mo", "For shipping", GOLD),
            ("Enterprise", "$200", "mo", "For scaling", SECONDARY),
        ]
        
        self.add_subcaption("Four tiers. No surprises. Pay for what you use.", duration=3)
        
        group = VGroup()
        for i, (name, price, period, desc, color) in enumerate(tiers):
            tier_box = VGroup(
                Text(name, font_size=22, color=color, font=MONO, weight=BOLD),
                VGroup(
                    Text(price, font_size=40, color=WHITE, font=MONO, weight=BOLD),
                    Text(f"/{period}", font_size=16, color=DIM, font=MONO),
                ).arrange(RIGHT, buff=0.05),
                Text(desc, font_size=16, color=DIM, font=MONO),
            ).arrange(DOWN, buff=0.15)
            
            x_offset = (i - 1.5) * 3.5
            tier_box.move_to(DOWN * 0.5 + RIGHT * x_offset)
            group.add(tier_box)
        
        for tier in group:
            self.play(GrowFromCenter(tier), run_time=0.5)
        
        self.wait(2.0)
        self.play(FadeOut(Group(title, group)), run_time=0.5)


class Scene7_Providers(Scene):
    """GPU providers earn money"""
    def construct(self):
        self.camera.background_color = BG
        
        title = Text("Got a GPU?", font_size=48, color=SECONDARY, font=MONO, weight=BOLD)
        title.move_to(UP * 1.5)
        
        subtitle = Text("Earn money while you sleep.", font_size=28, color=ACCENT, font=MONO)
        subtitle.move_to(UP * 0.3)
        
        self.add_subcaption("If you have a GPU sitting idle, Sawyer puts it to work.", duration=3)
        self.play(Write(title), run_time=1.2)
        self.play(Write(subtitle), run_time=0.8)
        
        steps = [
            ("1.", "Install Sawyer node", PRIMARY),
            ("2.", "Point it at your GPU", PRIMARY),
            ("3.", "Earn 70% of inference revenue", ACCENT),
        ]
        
        step_group = VGroup()
        for i, (num, text, color) in enumerate(steps):
            s = VGroup(
                Text(num, font_size=28, color=color, font=MONO, weight=BOLD),
                Text(text, font_size=24, color=WHITE, font=MONO),
            ).arrange(RIGHT, buff=0.2)
            s.move_to(DOWN * (0.8 + i * 0.8))
            step_group.add(s)
        
        for s in step_group:
            self.play(FadeIn(s, shift=RIGHT * 0.3), run_time=0.4)
        
        self.wait(1.0)
        
        callout = Text("RTX 3090 = ~$0.44/hr on RunPod", font_size=20, color=DIM, font=MONO)
        callout.move_to(DOWN * 3.5)
        self.play(FadeIn(callout), run_time=0.5)
        self.add_subcaption("An RTX 3090 earns about forty-four cents an hour. A rig with four of them? That's passive income.", duration=4)
        
        self.wait(2.0)
        self.play(FadeOut(Group(title, subtitle, step_group, callout)), run_time=0.5)


class Scene8_CTA(Scene):
    """Call to action"""
    def construct(self):
        self.camera.background_color = BG
        
        # Big URL
        url = Text("sawyer.infill.systems", font_size=52, color=PRIMARY, font=MONO, weight=BOLD)
        url.move_to(UP * 1.5)
        
        tagline = Text("Cheap inference. Distributed power.", font_size=28, color=WHITE, font=MONO)
        tagline.move_to(UP * 0.0)
        
        sub = Text("Start at $5/mo. No commitments.", font_size=22, color=ACCENT, font=MONO)
        sub.move_to(DOWN * 1.0)
        
        provider = Text("Got a GPU? Earn money at sawyer.infill.systems/provider", font_size=18, color=DIM, font=MONO)
        provider.move_to(DOWN * 2.5)
        
        brand = Text("InFill Systems, LLC", font_size=16, color=DIM, font=MONO)
        brand.move_to(DOWN * 3.5)
        
        self.add_subcaption("Start building at sawyer.infill.systems. Explorer tier is five dollars a month. No commitments.", duration=4)
        self.play(Write(url), run_time=1.5)
        self.play(Write(tagline), run_time=0.8)
        self.wait(0.5)
        self.play(FadeIn(sub), run_time=0.5)
        self.wait(1.0)
        self.play(FadeIn(provider), run_time=0.5)
        self.play(FadeIn(brand), run_time=0.3)
        self.wait(3.0)
        self.play(FadeOut(Group(url, tagline, sub, provider, brand)), run_time=0.5)