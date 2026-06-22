export function reduceAuth(state, action) {
  switch (action.type) {
    case 'authed': return { status: 'authed', user: action.user }
    case 'unauthed': return { status: 'login', user: null }
    case 'loading': return { status: 'loading', user: null }
    default: return state
  }
}

export function isAuthError(error) {
  return Boolean(error && error.response && error.response.status === 401)
}
