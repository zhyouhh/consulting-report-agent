import toast from 'react-hot-toast'

export const showSuccess = (message) => {
  toast.success(message, {
    duration: 3000,
    style: {
      background: '#1a1a2e',
      color: '#64ffda',
      border: '1px solid #2a2a4a',
    },
  })
}

export const showError = (message) => {
  toast.error(message, {
    duration: 4000,
    style: {
      background: '#1a1a2e',
      color: '#ff6b6b',
      border: '1px solid #2a2a4a',
    },
  })
}

export const showInfo = (message) => {
  toast(message, {
    duration: 3000,
    style: {
      background: '#1a1a2e',
      color: '#e2e2f0',
      border: '1px solid #2a2a4a',
    },
  })
}
