import { codeFromHash, exchangeCode, hashWithoutCode, urlWithoutCode } from '../src/login-code'

test('a sign-in link carries its code in the hash', () => {
  expect(codeFromHash('#/login?code=abc_-123')).toBe('abc_-123')
  expect(codeFromHash('#/login')).toBeNull()
  expect(codeFromHash('#/login?code=')).toBeNull()
  expect(codeFromHash('')).toBeNull()
})

test('the code leaves the URL, everything else stays', () => {
  expect(hashWithoutCode('#/login?code=abc')).toBe('#/login')
  expect(hashWithoutCode('#/login?code=abc&next=x')).toBe('#/login?next=x')
  expect(urlWithoutCode({ pathname: '/agent/admin/', search: '', hash: '#/login?code=abc' })).toBe('/agent/admin/#/login')
})

test('a good code signs in and is stripped', async () => {
  const sent: string[] = []
  let stripped = 0
  const r = await exchangeCode(
    'good',
    async (c) => void sent.push(c),
    () => stripped++,
  )
  expect(r).toEqual({ ok: true })
  expect(sent).toEqual(['good'])
  expect(stripped).toBe(1)
})

test('a spent code fails, and is stripped all the same', async () => {
  let stripped = 0
  const failure = Object.assign(new Error('expired'), { status: 401, code: 'login_code' })
  const r = await exchangeCode(
    'spent',
    () => Promise.reject(failure),
    () => stripped++,
  )
  expect(r).toEqual({ ok: false, error: failure })
  expect(stripped).toBe(1)
})
