"use client";

import { LandingNav } from "./LandingNav";
import { HeroSection } from "./HeroSection";
import { FeaturesGrid } from "./FeaturesGrid";
import { HowItWorks } from "./HowItWorks";
import { GetStarted } from "./GetStarted";
import { TechOverview } from "./TechOverview";
import { LandingFooter } from "./LandingFooter";

export function LandingPage() {
  return (
    <div className="min-h-screen bg-[#0f172a] text-[#f1f5f9] overflow-x-hidden">
      <LandingNav />
      <HeroSection />
      <FeaturesGrid />
      <HowItWorks />
      <GetStarted />
      <TechOverview />
      <LandingFooter />
    </div>
  );
}
