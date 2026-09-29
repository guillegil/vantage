import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { RecordingGuide } from './RecordingGuide';

describe('RecordingGuide', () => {
  it('on a server with accounts, asks for a token, then gives the exact command', () => {
    render(<RecordingGuide project="firmware" server="https://vantage.bench:8765" />);
    expect(screen.getByRole('heading', { name: 'Record a run into firmware' })).toBeInTheDocument();
    expect(screen.getByText('Get a token that can record')).toBeInTheDocument();
    expect(
      screen.getByText(
        'pytest --vantage --vantage-server https://vantage.bench:8765 --vantage-project firmware',
      ),
    ).toBeInTheDocument();
    expect(screen.getByLabelText('pytest.ini settings')).toHaveTextContent(
      '[pytest] vantage_server = https://vantage.bench:8765 vantage_project = firmware',
      { normalizeWhitespace: true },
    );
  });

  it('on an open server, needs no token, and leaves out what changes nothing', () => {
    render(<RecordingGuide project="default" server="http://127.0.0.1:8765" open title={false} />);
    expect(screen.queryByText('Get a token that can record')).toBeNull();
    expect(screen.getByText('pytest --vantage')).toBeInTheDocument();
    expect(screen.queryByLabelText('pytest.ini settings')).toBeNull();
    expect(screen.queryByRole('heading')).toBeNull();
  });

  it('tells a viewer who can let them record', () => {
    render(<RecordingGuide project="firmware" server="http://x" canRecord={false} />);
    expect(screen.getByText('Your role here can read runs, not record them')).toBeInTheDocument();
    expect(
      screen.getByText(
        'Viewers read firmware’s runs. An owner of firmware can make you an editor.',
      ),
    ).toBeInTheDocument();
  });
});
