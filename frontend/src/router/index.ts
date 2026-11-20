import { createRouter, createWebHistory } from 'vue-router'

import CaptureView from '@/views/CaptureView.vue'
import Overview from '@/views/Overview.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    {
      path: '/text_source_dataset',
      name: 'text-source-dataset',
      component: CaptureView,
      props: { moduleKey: 'text_source_dataset' },
    },
    {
      path: '/backfill_list',
      name: 'backfill-list',
      component: CaptureView,
      props: { moduleKey: 'backfill_list' },
    },
    {
      path: '/:moduleKey',
      name: 'module',
      component: WorkbenchView,
      props: true,
    },
  ],
})

export default router
