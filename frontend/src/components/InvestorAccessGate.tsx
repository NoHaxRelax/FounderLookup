import { KeyOutlined, SafetyCertificateOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Form, Input } from 'antd'
import { useState, type FormEvent } from 'react'

interface InvestorAccessGateProps {
  error?: string
  onUnlock: (credential: string) => void
}

export function InvestorAccessGate({ error, onUnlock }: InvestorAccessGateProps) {
  const [credential, setCredential] = useState('')
  const [validationError, setValidationError] = useState('')

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const normalized = credential.trim()
    if (!normalized) {
      setValidationError('Enter the investor access key.')
      return
    }
    setValidationError('')
    onUnlock(normalized)
  }

  return (
    <div className="page page--access">
      <section className="investor-access" aria-labelledby="investor-access-title">
        <Card className="investor-access__card">
          <p className="eyebrow">Protected investor workspace</p>
          <h1 id="investor-access-title" data-page-title tabIndex={-1}>Enter your access key</h1>
          <p className="lede" id="investor-access-description">
            The key stays in this tab&apos;s session, is sent only in the Authorization header,
            and is never compiled into the frontend bundle.
          </p>

          {(error || validationError) && (
            <Alert
              className="error-summary"
              type="error"
              showIcon
              role="alert"
              title="Workspace remains locked"
              description={validationError || error}
            />
          )}

          <Form className="investor-access__form" layout="vertical" onSubmitCapture={submit}>
            <Form.Item label="Investor access key" htmlFor="investor-access-key" required>
              <Input.Password
                id="investor-access-key"
                name="investorAccessKey"
                value={credential}
                onChange={(event) => setCredential(event.target.value)}
                autoComplete="off"
                aria-describedby="investor-access-description investor-access-help"
                prefix={<KeyOutlined aria-hidden="true" />}
                required
              />
            </Form.Item>
            <p id="investor-access-help" className="muted">
              Closing this tab ends access. Use a TLS URL for any non-local API.
            </p>
            <Button type="primary" htmlType="submit" size="large" block>
              Unlock investor workspace
            </Button>
          </Form>

          <p className="investor-access__safety">
            <SafetyCertificateOutlined aria-hidden="true" /> Founder application and private
            status routes remain public and never ask for this key.
          </p>
        </Card>
      </section>
    </div>
  )
}
