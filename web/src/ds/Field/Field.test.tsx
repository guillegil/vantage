import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { Field } from './Field';

it('labels its input, and describes it by its hint and error', () => {
  render(
    <Field
      label="Username"
      mono
      hint="As an admin made it"
      error="Required"
      autoComplete="username"
    />,
  );
  const input = screen.getByLabelText('Username');
  expect(input).toHaveClass('dl-input--mono');
  expect(input).toHaveAttribute('dir', 'ltr');
  expect(input).toHaveAttribute('autocomplete', 'username');
  expect(input).toHaveAttribute('aria-invalid', 'true');
  expect(input).toHaveAccessibleDescription('As an admin made it Required');
  expect(screen.getByRole('alert')).toHaveTextContent('Required');
});

it('gives two fields ids of their own', () => {
  render(
    <>
      <Field label="One" />
      <Field label="Two" />
    </>,
  );
  expect(screen.getByLabelText('One').id).not.toBe(screen.getByLabelText('Two').id);
});

it('renders a select', () => {
  render(
    <Field label="Role" as="select" options={['viewer', { value: 'editor', label: 'Editor' }]} />,
  );
  expect(screen.getByRole('combobox', { name: 'Role' })).toHaveTextContent('viewerEditor');
});
