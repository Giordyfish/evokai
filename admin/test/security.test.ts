import { classifierIn, policyFrom, settingsOf, UNSET, withClassifier } from '../src/security'

test('the policy keeps only what was chosen', () => {
  expect(policyFrom('default', { 'input.injection': 'default', 'output.secrets': 'enforce' }, {})).toEqual({
    modes: { 'output.secrets': 'enforce' },
  })
  expect(policyFrom('monitor', {}, { tools: { deny: ['order_refund'] } })).toEqual({ mode: 'monitor', tools: { deny: ['order_refund'] } })
})

test('the settings are the policy without its modes', () => {
  expect(settingsOf({ mode: 'enforce', modes: { scope: 'off' }, links: { allow: ['example.org'] } })).toEqual({ links: { allow: ['example.org'] } })
})

test('the classifier is read from the settings, the old boolean included', () => {
  expect(classifierIn('{}')).toEqual({ choice: UNSET, maxChars: null })
  expect(classifierIn('{"injection": {"classifier": true}}')).toEqual({ choice: 'default', maxChars: null })
  expect(classifierIn('{"injection": {"classifier": false, "classifier_max_chars": 5000}}')).toEqual({ choice: 'none', maxChars: 5000 })
  expect(classifierIn('{"injection": {"classifier": "prompt_guard"}}')?.choice).toBe('prompt_guard')
  expect(classifierIn('{"injection": {"threshold": 4}}')?.choice).toBe(UNSET)
  expect(classifierIn('{oops')).toBeNull()
})

test('choosing a classifier changes only its own fields', () => {
  const next = withClassifier('{"links": {"allow": ["example.org"]}, "injection": {"threshold": 4}}', { choice: 'bedrock_guardrails' })
  expect(JSON.parse(next!)).toEqual({ links: { allow: ['example.org'] }, injection: { threshold: 4, classifier: 'bedrock_guardrails' } })
  expect(JSON.parse(withClassifier(next!, { maxChars: 12000 })!).injection).toEqual({ threshold: 4, classifier: 'bedrock_guardrails', classifier_max_chars: 12000 })
  expect(JSON.parse(withClassifier('{"injection": {"classifier": "none"}}', { choice: UNSET })!)).toEqual({})
  expect(withClassifier('{oops', { choice: 'none' })).toBeNull()
})
