/**
 * ESLint 配置（依据技术文档 7.4「质量门禁」：eslint 零 error）
 *
 * 说明：
 *   采用 .eslintrc.cjs（ESLint 8 传统配置），与 package.json 的 eslint@^8.57 匹配。
 *   TypeScript 规则由 @typescript-eslint 提供，React Hooks 规则由官方插件提供。
 */

module.exports = {
  root: true,
  env: {
    browser: true,
    es2022: true,
  },
  parser: '@typescript-eslint/parser',
  parserOptions: {
    ecmaVersion: 'latest',
    sourceType: 'module',
    ecmaFeatures: { jsx: true },
  },
  settings: {
    react: { version: '18.3' },
  },
  plugins: ['@typescript-eslint', 'react-hooks', 'react-refresh'],
  extends: ['eslint:recommended', 'plugin:@typescript-eslint/recommended', 'plugin:react-hooks/recommended'],
  ignorePatterns: ['dist', 'node_modules', '.eslintrc.cjs', 'vite.config.ts'],
  rules: {
    // 仅允许导出组件，避免 React Fast Refresh 失效（类型导出不受影响）
    'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
    // 未使用变量交由 TS 规则统一处理
    'no-unused-vars': 'off',
    '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
  },
};
