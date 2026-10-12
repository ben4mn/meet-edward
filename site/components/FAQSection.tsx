'use client';

import { useState } from 'react';

const FAQ_ITEMS = [
  {
    question: 'What is Edward?',
    answer: 'Edward is an open-source personal AI assistant that runs on your own Mac for one person. He remembers what you tell him (and where he heard everything else), answers in an app, by voice and in iMessage, schedules his own follow-ups when he promises to check back, and works in a sandboxed shell and browser of his own.',
  },
  {
    question: 'Is my data private?',
    answer: 'Edward keeps his memories, conversations, documents and files in your own PostgreSQL database on your Mac. Every model call goes to the Claude API, which sees what that call sends: the conversation, the memories retrieved for it and the tool results. Voice (ElevenLabs), Gmail (Google), Twilio and Brave Search are only used if you set them up.',
  },
  {
    question: 'What AI models does Edward use?',
    answer: 'The Claude API, with a model per purpose: Sonnet 5.5 for conversations, his wakes and his screens, Haiku 5.5 for memory work and noticing, and Opus 5.5 for evaluations. You bring your own Anthropic API key, and each purpose can be changed in .env.',
  },
  {
    question: 'What does it cost?',
    answer: 'Edward is free and open source under Apache 2.0. You pay Anthropic for what he uses, and he stops himself at a daily spend cap you set ($5 by default): background work stops first, then his own turns, while your own turns can run into a small reserve. The app shows each day’s spend.',
  },
  {
    question: 'What do I need to run it?',
    answer: 'A Mac with Apple silicon that can stay on (a Mac mini makes a good host), Homebrew, the Xcode command-line tools and an Anthropic API key. Intel Macs, Linux and Windows are not supported: Edward depends on macOS for his sandbox, Messages, Mail, Calendar and launchd.',
  },
  {
    question: 'Can Edward send messages on my behalf?',
    answer: 'Yes, once you let him read Messages: he sends from your own iMessage account, signed as Edward. He writes only to people you asked him to, the chat he was asked in, or someone who addressed him in the last day. Friends can ask him with @Edward, inside limits, and unknown numbers wait for you.',
  },
  {
    question: 'Will he do things without asking?',
    answer: 'A little, and visibly. He keeps promises he made (checking back when he said he would), runs a silent nightly and one small block of free time a day that reaches nobody, and writes a journal entry, which you can mark right or wrong, whenever he acts on his own. Speaking up about what he notices in your messages is off until you turn it on.',
  },
  {
    question: 'Is his shell safe?',
    answer: 'It runs under a macOS Seatbelt sandbox that keeps him from his own keys, his running code, his databases and your other apps. It is not a containment boundary: the shell runs as your macOS user. For strong isolation, run Edward as a separate macOS user; the docs walk through it.',
  },
  {
    question: 'I run the March 2026 release. Can I upgrade?',
    answer: 'Yes. Your memories, conversations, documents, files and scheduled events are kept and migrated in place on the first start. Back up first, then follow the upgrade guide in the docs.',
  },
];

export default function FAQSection() {
  const [openIndex, setOpenIndex] = useState<number | null>(null);

  const jsonLd = {
    '@context': 'https://schema.org',
    '@type': 'FAQPage',
    mainEntity: FAQ_ITEMS.map(item => ({
      '@type': 'Question',
      name: item.question,
      acceptedAnswer: {
        '@type': 'Answer',
        text: item.answer,
      },
    })),
  };

  return (
    <section className="py-24 px-6">
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />
      <div className="max-w-3xl mx-auto">
        <h2 className="text-3xl font-bold text-center text-white mb-12">
          Frequently Asked Questions
        </h2>
        <div className="space-y-4">
          {FAQ_ITEMS.map((item, index) => (
            <div
              key={index}
              className="border border-white/10 rounded-lg overflow-hidden"
            >
              <button
                onClick={() => setOpenIndex(openIndex === index ? null : index)}
                className="w-full flex items-center justify-between p-5 text-left text-white hover:bg-white/5 transition-colors"
              >
                <span className="font-medium pr-4">{item.question}</span>
                <span className="text-white/50 shrink-0">
                  {openIndex === index ? '−' : '+'}
                </span>
              </button>
              {openIndex === index && (
                <div className="px-5 pb-5 text-white/70 leading-relaxed">
                  {item.answer}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
