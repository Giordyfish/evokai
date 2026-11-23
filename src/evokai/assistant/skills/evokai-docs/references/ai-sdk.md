# Use Evokai from the AI SDK

A page built with the [AI SDK](https://ai-sdk.dev) (`useChat`) can talk to an Evokai agent through your backend: the
backend route calls Evokai's [OpenAI-compatible API](openai-compatible.md) with the AI SDK's OpenAI-compatible
provider and returns the stream to `useChat`. Checked against `ai` 7.0.172, `@ai-sdk/openai-compatible` 3.0.94 and
`@ai-sdk/react` 4.

**It is text chat.** The OpenAI-compatible API carries the agent's answer as text, so this route does not carry what
Evokai's own session stream does: no confirmation buttons (tools that change data are not offered, unless you opt in
to [confirmations in text](openai-compatible.md#confirmations-in-text), which then come as a question to answer in
words), no [client tools](client-tools.md), no charts, diagrams or other data parts, no usage shares, no voice. For
those, use the session API with the [TypeScript client](https://github.com/Giordyfish/evokai/tree/main/clients/typescript)
or `<evokai-chat>`.

## The backend route

```bash
npm i ai @ai-sdk/openai-compatible @ai-sdk/react
```

```ts filename='app/api/chat/route.ts'
import { createOpenAICompatible } from '@ai-sdk/openai-compatible'
import { convertToModelMessages, createUIMessageStreamResponse, streamText, toUIMessageStream, type UIMessage } from 'ai'

// The server key stays on the server: never in the browser.
const evokai = createOpenAICompatible({
  name: 'evokai',
  baseURL: `${process.env.EVOKAI_URL}/v1/openai`, // e.g. https://agents.example.com/agent/v1/openai
  apiKey: process.env.EVOKAI_SERVER_KEY,
})

export async function POST(req: Request) {
  const user = await signedInUser(req) // your own authentication
  const { messages }: { messages: UIMessage[] } = await req.json()
  const result = streamText({
    model: evokai.chatModel('assistant'), // the agent's name; it must allow the server channel
    messages: await convertToModelMessages(messages),
    providerOptions: { evokai: { user: user.id } }, // the subject: usage, caps and memory are theirs
  })
  return createUIMessageStreamResponse({ stream: toUIMessageStream({ stream: result.stream }) })
}
```

- **The agent** is the model name, and it must allow the `server` channel (`channels: [browser, server]`), as for every
  call made with the server key.
- **The subject** is the request's `user` (here `providerOptions.evokai.user`): your own id of the signed-in person,
  which Evokai uses for usage, caps and abuse limits. Without it the subject is `system:<agent>`.
- **The conversation** is the one `useChat` keeps: every request sends it whole, and Evokai stores nothing of it. A
  system prompt from the route is ignored: the agent's own instructions are its system prompt.
- **Streaming** comes as Evokai streams Chat Completions: token by token, or sentence by sentence when an output
  control of the security policy is enforced (each sentence passes the guard first). An error, a cap reached or the
  guard stopping the answer arrives as an error the AI SDK shows in `useChat`'s `error`.

## The page

```tsx filename='app/page.tsx'
'use client'
import { useChat } from '@ai-sdk/react'
import { useState } from 'react'

export default function Chat() {
  const [input, setInput] = useState('')
  const { messages, sendMessage, status } = useChat() // posts to /api/chat
  return (
    <>
      {messages.map((m) => (
        <div key={m.id}>
          {m.role === 'user' ? 'You: ' : 'Assistant: '}
          {m.parts.map((p, i) => (p.type === 'text' ? <span key={i}>{p.text}</span> : null))}
        </div>
      ))}
      <form
        onSubmit={(e) => {
          e.preventDefault()
          sendMessage({ text: input })
          setInput('')
        }}
      >
        <input value={input} onChange={(e) => setInput(e.target.value)} disabled={status !== 'ready'} />
      </form>
    </>
  )
}
```

The AI SDK's own tools, attachments and data parts are not sent to Evokai: a request with tools or a part that is not
text is refused (`tools_not_supported`, `unsupported_content`).
