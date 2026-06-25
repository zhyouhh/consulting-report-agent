import toast from 'react-hot-toast'

export const showSuccess = (message) => {
  toast.success(message, {
    duration: 3000,
    style: {
      background: 'rgb(var(--card))',
      color: 'rgb(var(--success))',
      border: '1px solid rgb(var(--border))',
    },
  })
}

export const showError = (message) => {
  toast.error(message, {
    duration: 4000,
    style: {
      background: 'rgb(var(--card))',
      color: 'rgb(var(--error))',
      border: '1px solid rgb(var(--border))',
    },
  })
}

export const showInfo = (message) => {
  toast(message, {
    duration: 3000,
    style: {
      background: 'rgb(var(--card))',
      color: 'rgb(var(--text))',
      border: '1px solid rgb(var(--border))',
    },
  })
}
