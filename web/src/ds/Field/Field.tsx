import { type ChangeEvent, createElement } from 'react';
import type { FieldProps } from '../contract';
import { Icon } from '../Icon/Icon';
import { cx } from '../lib/cx';
import { useUid } from '../lib/hooks';

interface SelectBoxProps {
  id?: string;
  label?: string;
  options?: FieldProps['options'];
  value?: FieldProps['value'];
  defaultValue?: FieldProps['defaultValue'];
  onChange?: (e: ChangeEvent<HTMLSelectElement>) => void;
  mono?: boolean;
  size?: 'sm' | 'md';
  disabled?: boolean;
  className?: string;
}

function SelectBox(p: SelectBoxProps) {
  const controlled = typeof p.onChange === 'function';
  return (
    <span className={cx('dl-select', p.size === 'sm' && 'dl-select--sm', p.className)}>
      <select
        className={cx('dl-input', p.mono && 'dl-input--mono')}
        id={p.id}
        aria-label={p.label}
        disabled={p.disabled}
        value={controlled ? (p.value as string) : undefined}
        defaultValue={
          controlled ? undefined : ((p.value != null ? p.value : p.defaultValue) as string)
        }
        onChange={controlled ? p.onChange : undefined}
      >
        {(p.options || []).map((o) => {
          const v = typeof o === 'string' ? o : o.value;
          return (
            <option key={v} value={v}>
              {typeof o === 'string' ? o : o.label}
            </option>
          );
        })}
      </select>
      <Icon name="chevron-down" size={16} />
    </span>
  );
}

export function Field(p: FieldProps) {
  const auto = useUid('dl-f');
  const id = p.id || auto;
  const hintId = p.hint ? `${id}-hint` : undefined;
  const errId = p.error ? `${id}-err` : undefined;
  const describedBy = [hintId, errId].filter(Boolean).join(' ') || undefined;
  const { label, hint, error, as, mono, options, className, size, id: _id, ...rest } = p;
  let control: ReturnType<typeof createElement>;
  if (as === 'select') {
    control = (
      <SelectBox
        id={id}
        options={options}
        value={p.value}
        defaultValue={p.defaultValue}
        onChange={p.onChange as SelectBoxProps['onChange']}
        mono={mono}
        size={size}
        disabled={p.disabled}
      />
    );
  } else {
    control = createElement(as === 'textarea' ? 'textarea' : 'input', {
      ...rest,
      id,
      className: cx('dl-input', mono && 'dl-input--mono'),
      dir: mono ? 'ltr' : undefined,
      'aria-invalid': error ? 'true' : undefined,
      'aria-describedby': describedBy,
    });
  }
  return (
    <div className={cx('dl-field', className)}>
      {label ? (
        <label className="dl-field__label" htmlFor={id}>
          {label}
        </label>
      ) : null}
      {control}
      {hint ? (
        <span className="dl-field__hint" id={hintId}>
          {hint}
        </span>
      ) : null}
      {error ? (
        <span className="dl-field__error" id={errId} role="alert">
          <Icon name="alert" size={14} />
          {error}
        </span>
      ) : null}
    </div>
  );
}
