'use client';

import { useState } from 'react';

const FAQ_ITEMS = [
  {
    question: 'What is Edward?',
    answer: 'Edward is an open-source AI assistant with persistent long-term memory. Unlike ChatGPT or Claude, Edward remembers every conversation, learns your preferences over time, and runs entirely on your own machine.',
  },
  {
    question: 'Is my data private?',
    answer: 'Yes. Edward runs locally on your hardware. Your conversations, memories, and documents never leave your machine. You own your data completely.',
  },
  {
    question: 'What AI models does Edward use?',
    answer: "Edward uses Anthropic's Claude models via API. You bring your own API key, so you have full control over costs and model selection.",
  },
  {
    question: 'How is Edward different from ChatGPT?',
    answer: 'Three key differences: Edward remembers everything across conversations using persistent vector memory. Edward can proactively monitor your messages, calendar, and email. And Edward runs on your machine — your data stays private.',
  },
  {
    question: 'Can Edward send messages on my behalf?',
    answer: 'Yes. Edward integrates with iMessage, SMS, and WhatsApp. It can send messages, respond to incoming messages, and even schedule future messages and reminders.',
  },
  {
    question: 'Is Edward free to use?',
    answer: 'Edward is free and open-source under the Apache 2.0 license. The only cost is your Anthropic API usage, which you pay directly to Anthropic.',
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
