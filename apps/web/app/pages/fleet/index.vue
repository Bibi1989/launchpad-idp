<script setup lang="ts">
const { t } = useI18n()

useHead({ title: () => `${t('hybrid.title')} - ${t('brand.product')}` })
</script>

<template>
  <HybridProvisioner />
</template>
