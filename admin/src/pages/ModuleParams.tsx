/**
 * The parameters of a prompt module as a form, generated from its schema: a choice is a select, a yes/no a switch, a
 * short text an input with its limit, a number a number input, a multi-choice a row of switches. A parameter outside
 * the subset is not rendered: the page shows the service's check instead.
 */
import { choices, controlFor, propsOf, unsupportedParams } from '../modules'
import { Field, Switch } from '../ui'
import { t } from '../i18n'

type Values = Record<string, unknown>

/** The text of a default, as the form shows it next to an empty control. */
const shown = (v: unknown): string => (Array.isArray(v) ? v.join(', ') : v === true ? t('mod.yes') : v === false ? t('mod.no') : String(v ?? ''))

export function ParamsForm(p: { schema: unknown; values: Values; onChange: (v: Values) => void; disabled?: boolean; idPrefix?: string }) {
  const set = (name: string, v: unknown) => {
    const next = { ...p.values }
    if (v === undefined || v === '') delete next[name]
    else next[name] = v
    p.onChange(next)
  }
  const bad = unsupportedParams(p.schema)
  const props = propsOf(p.schema)
  if (!props.length) return <p class="hint">{t('mod.noParams')}</p>
  return (
    <div class="grid g2" style="gap:0 14px">
      {props.map(([name, prop]) => {
        const control = controlFor(prop)
        if (!control) return null
        const id = `${p.idPrefix ?? 'mp'}-${name}`
        const value = p.values[name]
        const def = prop.default
        const hint = [typeof prop.description === 'string' ? prop.description : '', def !== undefined ? t('mod.default', { value: shown(def) }) : '']
          .filter(Boolean)
          .join(', ')
        if (control === 'select')
          return (
            <Field key={name} label={<code>{name}</code>} id={id} hint={hint || undefined}>
              <select id={id} class="in" value={typeof value === 'string' ? value : ''} disabled={p.disabled} onChange={(e) => set(name, e.currentTarget.value)}>
                <option value="">{t('mod.useDefault')}</option>
                {choices(prop).map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </Field>
          )
        if (control === 'switch')
          return (
            <Field key={name} label={<code>{name}</code>} hint={hint || undefined}>
              <Switch on={value === undefined ? def === true : value === true} disabled={p.disabled} onChange={(v) => set(name, v)} aria={name} />
            </Field>
          )
        if (control === 'number')
          return (
            <Field key={name} label={<code>{name}</code>} id={id} hint={hint || undefined}>
              <input
                id={id}
                class="in"
                type="number"
                min={typeof prop.minimum === 'number' ? prop.minimum : undefined}
                max={typeof prop.maximum === 'number' ? prop.maximum : undefined}
                step={prop.type === 'integer' ? 1 : 'any'}
                value={typeof value === 'number' ? String(value) : ''}
                disabled={p.disabled}
                onInput={(e) => set(name, e.currentTarget.value === '' ? undefined : Number(e.currentTarget.value))}
              />
            </Field>
          )
        if (control === 'multi') {
          const picked = Array.isArray(value) ? (value as string[]) : Array.isArray(def) ? (def as string[]) : []
          return (
            <Field key={name} label={<code>{name}</code>} hint={hint || undefined}>
              <div class="flags">
                {choices(prop).map((c) => (
                  <Switch
                    key={c}
                    label={c}
                    on={picked.includes(c)}
                    disabled={p.disabled}
                    onChange={(on) => set(name, on ? [...picked, c] : picked.filter((x) => x !== c))}
                  />
                ))}
              </div>
            </Field>
          )
        }
        return (
          <Field key={name} label={<code>{name}</code>} id={id} hint={hint || undefined}>
            <input
              id={id}
              class="in"
              maxLength={typeof prop.maxLength === 'number' ? prop.maxLength : undefined}
              value={typeof value === 'string' ? value : ''}
              disabled={p.disabled}
              onInput={(e) => set(name, e.currentTarget.value)}
            />
          </Field>
        )
      })}
      {bad.length ? <p class="hint">{t('mod.unsupported', { names: bad.join(', ') })}</p> : null}
    </div>
  )
}
